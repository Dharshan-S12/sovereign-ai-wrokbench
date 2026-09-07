import os
import re
import json
import uuid
import httpx
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict, Any

from app.models.model_registry import ModelRole, ROLE_MODEL_PREFERENCES, _SIMULATED_UNAVAILABLE, _SIMULATED_UNTRUSTED
from app.router.lightweight_classifier import classify_intent_lightweight

ROUTER_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "storage", "router_decisions.jsonl")

DISAMBIGUATION_THRESHOLD = 0.65

# Tier usage counters for observability and performance metrics
ROUTER_TIER_COUNTS: Dict[str, int] = {
    "tier_1_fast_classifier": 0,
    "tier_2_llm_fallback": 0,
    "tier_3_disambiguation": 0
}

def record_tier_usage(routed_by: str):
    """Increment counters for router tier metrics."""
    if routed_by == "fast_classifier":
        ROUTER_TIER_COUNTS["tier_1_fast_classifier"] += 1
    elif routed_by == "llm_semantic_fallback":
        ROUTER_TIER_COUNTS["tier_2_llm_fallback"] += 1
    elif routed_by == "disambiguation":
        ROUTER_TIER_COUNTS["tier_3_disambiguation"] += 1

def get_router_tier_metrics() -> Dict[str, Any]:
    """Returns resolution counts and percentages across all 3 routing tiers."""
    t1 = ROUTER_TIER_COUNTS["tier_1_fast_classifier"]
    t2 = ROUTER_TIER_COUNTS["tier_2_llm_fallback"]
    t3 = ROUTER_TIER_COUNTS["tier_3_disambiguation"]
    total = t1 + t2 + t3
    return {
        "tier_1_count": t1,
        "tier_2_count": t2,
        "tier_3_count": t3,
        "total_queries": total,
        "tier_1_resolution_rate": round(t1 / total, 4) if total > 0 else 0.0,
        "tier_2_fallback_rate": round(t2 / total, 4) if total > 0 else 0.0,
        "tier_3_disambiguation_rate": round(t3 / total, 4) if total > 0 else 0.0,
    }

def reset_router_tier_metrics():
    """Resets the in-memory tier resolution counters."""
    ROUTER_TIER_COUNTS["tier_1_fast_classifier"] = 0
    ROUTER_TIER_COUNTS["tier_2_llm_fallback"] = 0
    ROUTER_TIER_COUNTS["tier_3_disambiguation"] = 0

@dataclass
class ModelChoice:
    model_name: str
    task_type: str
    reason: str
    system_prompt: Optional[str] = None
    downstream_tool: Optional[str] = None

@dataclass
class IntentRouteResult:
    task_type: str
    routing_reason: str
    model_name: str
    confidence: float
    is_ambiguous: bool = False
    suggested_options: List[Dict[str, str]] = field(default_factory=list)
    routed_by: str = "fast_classifier"  # "fast_classifier" | "llm_semantic_fallback" | "disambiguation"
    vocabulary_coverage: float = 1.0

def log_router_decision(
    prompt: str,
    chosen_intent: str,
    confidence: float,
    routing_reason: str,
    was_disambiguated: bool = False,
    confirmed_by_user: bool = False,
    routed_by: str = "fast_classifier",
    vocabulary_coverage: float = 1.0
) -> Dict[str, Any]:
    """
    Logs every autonomous routing decision to an append-only JSONL log for continuous accuracy auditing.
    """
    os.makedirs(os.path.dirname(ROUTER_LOG_PATH), exist_ok=True)
    decision_record = {
        "id": str(uuid.uuid4()),
        "prompt": prompt,
        "chosen_intent": chosen_intent,
        "confidence": round(confidence, 3),
        "routing_reason": routing_reason,
        "was_disambiguated": was_disambiguated,
        "confirmed_by_user": confirmed_by_user,
        "routed_by": routed_by,
        "vocabulary_coverage": round(vocabulary_coverage, 3),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    try:
        with open(ROUTER_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision_record) + "\n")
    except Exception as e:
        print(f"[Router Log Warning] Could not persist decision: {e}")
    return decision_record

def get_router_audit_records(limit: int = 100) -> List[Dict[str, Any]]:
    """Retrieves the recent router decision audit records."""
    if not os.path.exists(ROUTER_LOG_PATH):
        return []
    records = []
    try:
        with open(ROUTER_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line.strip()))
    except Exception:
        pass
    return records[-limit:]

def get_fast_reasoning_model() -> str:
    """Dynamically resolve candidate model for fast_reasoning role with fallback resilience."""
    candidates = ROLE_MODEL_PREFERENCES.get(ModelRole.FAST_REASONING.value, ["qwen2.5:3b", "qwen2.5:7b-instruct"])
    for cand in candidates:
        if cand in _SIMULATED_UNAVAILABLE or cand in _SIMULATED_UNTRUSTED:
            continue
        return cand
    return "qwen2.5:3b"

def llm_semantic_route(
    prompt_text: str,
    attached_file_info: Optional[str] = None,
    timeout_seconds: float = 45.0
) -> Tuple[str, float, str]:
    """
    Tier 2 LLM Semantic Fallback Router.
    Restores the generative reasoning router using the fast_reasoning role (qwen2.5:3b).
    Invoked when Tier 1 classifier encounters novel vocabulary or uncertain confidence (< 0.65).
    Returns: (intent, confidence, reasoning)
    """
    model_name = get_fast_reasoning_model()
    attachment_context = f"\nAttached file: {os.path.basename(attached_file_info)}" if attached_file_info else ""

    system_prompt = """You are an intent routing classifier for an industrial refinery sovereign workbench.
Classify the user request into exactly ONE of the following intents:
- doc_gen: drafting compliance memo, executive report, SOP, or Word document (.docx)
- predictive_trend: trend forecast, degradation analysis, remaining useful life, days to breach, predictive trend
- rule_check: evaluate measurements against ISO 10816 / SOP limits
- code_exec: mathematical calculation, Python script synthesis, prime numbers, numerical computation
- cross_doc_query: historical tasks across previous inspection reports, past database records
- ocr: extract text or read tables from attached document or image
- text_gen: general engineering knowledge Q&A, conceptual definitions, explanations
- disambiguation: request is genuinely underspecified or ambiguous with multiple plausible interpretations (e.g. 'check pump', 'status', 'help')

Respond ONLY with a JSON object in this format:
{"intent": "<intent_name>", "confidence": <float between 0.0 and 1.0>, "reasoning": "<brief explanation>"}"""

    user_content = f'User request: "{prompt_text}"{attachment_context}'

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(
                "http://127.0.0.1:11434/api/generate",
                json={
                    "model": model_name,
                    "prompt": user_content,
                    "system": system_prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.0}
                }
            )
            if resp.status_code == 200:
                raw_resp = resp.json().get("response", "")
                parsed = json.loads(raw_resp)
                intent = str(parsed.get("intent", "")).lower().strip()
                confidence = float(parsed.get("confidence", 0.5))
                reasoning = str(parsed.get("reasoning", "LLM semantic reasoning"))

                valid_intents = {
                    "doc_gen", "predictive_trend", "rule_check", "code_exec",
                    "cross_doc_query", "ocr", "text_gen", "disambiguation"
                }
                if intent in valid_intents:
                    return intent, round(confidence, 3), reasoning
    except Exception as e:
        print(f"[Tier 2 LLM Fallback Warning] Call failed: {e}")

    return "disambiguation", 0.40, "LLM fallback service unavailable or non-responsive"

def _resolve_target_model(intent: str) -> str:
    if intent == "ocr":
        return "qwen2.5vl:7b"
    elif intent == "code_exec":
        return "qwen2.5-coder:3b"
    return "qwen2.5:3b"

def _get_routing_reason(intent: str) -> str:
    if intent == "ocr":
        return "OCR & Document Intelligence: Vision Extraction Model selected"
    elif intent == "code_exec":
        return "Mathematical & Python Sandbox: Code Reasoning Model selected"
    elif intent == "doc_gen":
        return "Executive Compliance & SOP Generation: Multi-Agent DocGen Pipeline selected"
    elif intent == "predictive_trend":
        return "Physics-Grounded Trend Forecasting: Degradation Model selected"
    elif intent == "rule_check":
        return "Deterministic Rule Engine: ISO 10816 / SOP Standard Checker selected"
    elif intent == "cross_doc_query":
        return "Historical Cross-Document Ledger: Multi-Task Search selected"
    return "General Agent Reasoning & Synthesis selected"

def auto_detect_task_intent(
    prompt: str,
    file_path: Optional[str] = None,
    confirmed_intent: Optional[str] = None
) -> IntentRouteResult:
    """
    Three-Tier Routing Flow:
    1. Tier 1 — Fast deterministic classifier (regex + TF-IDF + coverage check). Zero LLM latency.
    2. Tier 2 — LLM semantic fallback (qwen2.5:3b via ModelRole.FAST_REASONING). Handles novel vocabulary.
    3. Tier 3 — Disambiguation UI (only when both Tier 1 and Tier 2 report ambiguity).
    """
    prompt_clean = (prompt or "").strip()

    # User explicitly confirmed an intent (e.g. from disambiguation button click)
    if confirmed_intent:
        c_intent = confirmed_intent.lower().strip()
        model_name = _resolve_target_model(c_intent)
        res = IntentRouteResult(
            task_type=c_intent,
            routing_reason=f"User-Confirmed Intent Selection: Routed to '{c_intent}'",
            model_name=model_name,
            confidence=1.0,
            is_ambiguous=False,
            routed_by="disambiguation",
            vocabulary_coverage=1.0
        )
        record_tier_usage("disambiguation")
        log_router_decision(prompt_clean, c_intent, 1.0, res.routing_reason, was_disambiguated=True, confirmed_by_user=True, routed_by="disambiguation")
        return res

    # 1. Adversarial Prompt Injection Guard
    if re.search(r"\b(system override|escalate|administrator|ignore routing|bypass security|database secrets)\b", prompt_clean, re.IGNORECASE):
        res = IntentRouteResult(
            task_type="text_gen",
            routing_reason="Adversarial Prompt Injection Blocked: Sanitized and routed to base reasoning engine without privilege escalation",
            model_name="qwen2.5:3b",
            confidence=0.85,
            is_ambiguous=False,
            routed_by="fast_classifier",
            vocabulary_coverage=1.0
        )
        record_tier_usage("fast_classifier")
        log_router_decision(prompt_clean, "text_gen", 0.85, res.routing_reason, routed_by="fast_classifier")
        return res

    # 2. Attachment / File Path Signal
    if file_path:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"]:
            # If user provided a prompt specifying an explicit downstream action on this document:
            if prompt_clean:
                p_intent, p_conf, _, _, p_cov = classify_intent_lightweight(prompt_clean, None)
                if p_intent in ["doc_gen", "predictive_trend", "code_exec", "cross_doc_query", "text_gen"] and p_conf >= 0.50:
                    routed_task = "doc_gen" if p_intent in ["doc_gen", "predictive_trend"] and any(w in prompt_clean.lower() for w in ["memo", "report", "compliance", "doc", "sop"]) else (
                        "code_exec" if p_intent == "predictive_trend" else p_intent
                    )
                    model_map = {
                        "doc_gen": "qwen2.5:3b",
                        "code_exec": "qwen2.5-coder:3b",
                        "text_gen": "qwen2.5:3b",
                        "cross_doc_query": "qwen2.5:3b"
                    }
                    target_model = model_map.get(routed_task, "qwen2.5:3b")
                    res = IntentRouteResult(
                        task_type=routed_task,
                        routing_reason=f"Attached Document ({os.path.basename(file_path)}) with Action '{routed_task}': Routed to {routed_task} using document as ground context",
                        model_name=target_model,
                        confidence=round(p_conf, 2),
                        is_ambiguous=False,
                        routed_by="fast_classifier",
                        vocabulary_coverage=p_cov
                    )
                    record_tier_usage("fast_classifier")
                    log_router_decision(prompt_clean, routed_task, p_conf, res.routing_reason, routed_by="fast_classifier", vocabulary_coverage=p_cov)
                    return res

            res = IntentRouteResult(
                task_type="ocr",
                routing_reason=f"Document/Image attached ({os.path.basename(file_path)}): Auto-routed to Vision OCR & Dynamic Document Intelligence",
                model_name="qwen2.5vl:7b",
                confidence=0.98,
                is_ambiguous=False,
                routed_by="fast_classifier",
                vocabulary_coverage=1.0
            )
            record_tier_usage("fast_classifier")
            log_router_decision(prompt_clean, "ocr", 0.98, res.routing_reason, routed_by="fast_classifier", vocabulary_coverage=1.0)
            return res

    # 3. TIER 1 — Fast Deterministic Intent Classifier
    t1_intent, t1_conf, t1_dist, t1_ambig, t1_cov = classify_intent_lightweight(prompt_clean, file_path)

    # If Tier 1 confidence is high (>= 0.65), unambiguous, and vocabulary coverage is sufficient (>= 0.60):
    if (t1_conf >= DISAMBIGUATION_THRESHOLD) and (not t1_ambig) and (t1_cov >= 0.60):
        target_model = _resolve_target_model(t1_intent)
        routing_reason = _get_routing_reason(t1_intent)
        res = IntentRouteResult(
            task_type=t1_intent,
            routing_reason=f"Deterministic Classifier ({t1_conf:.2f}, cov {t1_cov:.2f}): {routing_reason}",
            model_name=target_model,
            confidence=t1_conf,
            is_ambiguous=False,
            routed_by="fast_classifier",
            vocabulary_coverage=t1_cov
        )
        record_tier_usage("fast_classifier")
        log_router_decision(prompt_clean, t1_intent, t1_conf, res.routing_reason, routed_by="fast_classifier", vocabulary_coverage=t1_cov)
        return res

    # Check for inherently ambiguous short phrases before LLM fallback
    is_known_ambiguous = prompt_clean.lower().strip() in [
        "check the pump", "check pump", "inspect pump", "test system", "view status",
        "report", "check", "run", "analyze", "test", "status", "data", "file", "document", "calculate", "summary", "help"
    ]

    # 4. TIER 2 — LLM Semantic Fallback (Restored Generative Reasoning Router)
    # Invoked when Tier 1 confidence is low, ambiguous, or has low vocabulary coverage (unless known ambiguous)
    if not is_known_ambiguous:
        t2_intent, t2_conf, t2_reason = llm_semantic_route(prompt_clean, file_path)
    else:
        t2_intent, t2_conf, t2_reason = "disambiguation", 0.45, "Known ambiguous prompt requiring human disambiguation"

    if (t2_conf >= DISAMBIGUATION_THRESHOLD) and (t2_intent != "disambiguation"):
        target_model = _resolve_target_model(t2_intent)
        res = IntentRouteResult(
            task_type=t2_intent,
            routing_reason=f"LLM Semantic Fallback ({t2_conf:.2f}): {t2_reason}",
            model_name=target_model,
            confidence=t2_conf,
            is_ambiguous=False,
            routed_by="llm_semantic_fallback",
            vocabulary_coverage=t1_cov
        )
        record_tier_usage("llm_semantic_fallback")
        log_router_decision(prompt_clean, t2_intent, t2_conf, res.routing_reason, routed_by="llm_semantic_fallback", vocabulary_coverage=t1_cov)
        return res

    # 5. TIER 3 — Disambiguation UI (True Last Resort)
    # Only shown if BOTH Tier 1 AND Tier 2 report low confidence or ambiguity
    suggested_options = [
        {"task_type": "doc_gen", "label": "Draft Compliance Memo / SOP (.docx)", "description": "Draft formal Word memorandum with rule engine checks", "model_name": "qwen2.5:3b"},
        {"task_type": "predictive_trend", "label": "Forecast Equipment Degradation Trend", "description": "Predict days remaining to ISO Zone C/D breach", "model_name": "qwen2.5:3b"},
        {"task_type": "rule_check", "label": "Deterministic Rule Check", "description": "Evaluate measurements against ISO 10816-3 thresholds", "model_name": "qwen2.5:3b"},
        {"task_type": "code_exec", "label": "Run Python Calculation in Sandbox", "description": "Perform numerical RMS computation or data analysis in sandbox", "model_name": "qwen2.5-coder:3b"},
        {"task_type": "text_gen", "label": "General Agent Q&A & Reasoning", "description": "Ask engineering questions or get operational recommendations", "model_name": "qwen2.5:3b"}
    ]
    final_conf = round(min(t1_conf, 0.45), 2) if t1_conf > 0 else 0.45
    res = IntentRouteResult(
        task_type="disambiguation",
        routing_reason=f"Ambiguous query (Tier 1 conf: {t1_conf:.2f}, Tier 2 conf: {t2_conf:.2f}): Human disambiguation required",
        model_name="qwen2.5:3b",
        confidence=final_conf,
        is_ambiguous=True,
        suggested_options=suggested_options,
        routed_by="disambiguation",
        vocabulary_coverage=t1_cov
    )
    record_tier_usage("disambiguation")
    log_router_decision(prompt_clean, "disambiguation", final_conf, res.routing_reason, was_disambiguated=True, routed_by="disambiguation", vocabulary_coverage=t1_cov)
    return res

def route_task(task_type: str, input_ref: str) -> ModelChoice:
    """Synchronous fallback routing mapping task types to default model choices."""
    normalized_type = (task_type or "").lower()
    if normalized_type == "ocr":
        return ModelChoice(
            model_name="qwen2.5vl:7b",
            task_type="ocr",
            reason="Routed to vision model for document OCR and extraction"
        )
    elif normalized_type == "code_exec":
        return ModelChoice(
            model_name="qwen2.5-coder:3b",
            task_type="code_exec",
            reason="Routed to coding model for Python script synthesis and execution",
            system_prompt="You are an expert Python engineer and data analyst. Write clean, self-contained, executable code adhering to on-prem air-gapped constraints. Output markdown code blocks.",
            downstream_tool="sandbox_exec"
        )
    elif normalized_type == "cross_doc_query":
        return ModelChoice(
            model_name="qwen2.5:3b",
            task_type="cross_doc_query",
            reason="Routed to low-latency model for rapid cross-document synthesis"
        )
    elif normalized_type == "doc_gen":
        return ModelChoice(
            model_name="qwen2.5:3b",
            task_type="doc_gen",
            reason="Routed to text model for document drafting with downstream document writer tool",
            system_prompt="You are a sovereign confidential document synthesis agent. Generate well-structured technical/industrial documentation based on the user instructions.",
            downstream_tool="doc_writer"
        )
    else:
        return ModelChoice(
            model_name="qwen2.5:3b",
            task_type=normalized_type,
            reason=f"Default routing to fast text model for task type '{normalized_type}'"
        )
