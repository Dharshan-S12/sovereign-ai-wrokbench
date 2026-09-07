"""
MRPL Sovereign Workbench — Model Registry & Resilient Substitution Layer
Provides role-based model abstraction, integrity verification, ranked fallback cascades,
transparent audit logging of fallback events, and tiered memory lifecycle management.
"""

import os
import json
import uuid
import time
import httpx
from enum import Enum
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

from app.startup.model_integrity import is_model_trusted, verify_model_integrity

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
FALLBACK_LOG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "storage", "model_fallback_events.jsonl")
)

class ModelRole(str, Enum):
    FAST_REASONING = "fast_reasoning"
    VISION_OCR = "vision_ocr"
    DRAFTING = "drafting"
    CODING = "coding"
    GENERAL = "general"

@dataclass
class ModelResolution:
    model_name: str
    role: str
    is_fallback: bool
    primary_model: str
    reason: str
    timeout_seconds: float
    is_warm: bool = False

# Ranked fallback cascades per role
ROLE_MODEL_PREFERENCES: Dict[str, List[str]] = {
    ModelRole.FAST_REASONING.value: [
        "qwen2.5:3b",
        "qwen2.5:7b-instruct",
        "qwen2.5-coder:3b"
    ],
    ModelRole.VISION_OCR.value: [
        "qwen2.5vl:7b",
        "llava:7b",
        "llama3.2-vision:11b",
        "qwen2.5:3b"
    ],
    ModelRole.DRAFTING.value: [
        "qwen2.5:7b-instruct",
        "qwen2.5:3b",
        "deepseek-r1:1.5b"
    ],
    ModelRole.CODING.value: [
        "qwen2.5-coder:3b",
        "qwen2.5:3b",
        "qwen2.5:7b-instruct"
    ],
    ModelRole.GENERAL.value: [
        "qwen2.5:3b",
        "qwen2.5:7b-instruct",
        "deepseek-r1:1.5b"
    ]
}

# Role default timeouts in seconds (generous for cold-start and GPU/CPU swapping)
ROLE_TIMEOUTS: Dict[str, float] = {
    ModelRole.FAST_REASONING.value: 90.0,
    ModelRole.VISION_OCR.value: 120.0,
    ModelRole.DRAFTING.value: 180.0,
    ModelRole.CODING.value: 90.0,
    ModelRole.GENERAL.value: 120.0
}

# Tiering Configuration: Always-warm vs On-demand
WARM_TIER_MODELS = {"qwen2.5:3b"}
ON_DEMAND_TIER_MODELS = {"qwen2.5:7b-instruct", "qwen2.5vl:7b"}

# Cache of installed Ollama models
_installed_cache: List[str] = []
_installed_cache_time: float = 0.0

# Simulated test overrides (for regression testing of fallback paths)
_SIMULATED_UNAVAILABLE: List[str] = []
_SIMULATED_UNTRUSTED: List[str] = []

def set_simulated_model_status(unavailable: Optional[List[str]] = None, untrusted: Optional[List[str]] = None):
    """Allows test fixtures to simulate model dropouts or supply-chain tampering."""
    global _SIMULATED_UNAVAILABLE, _SIMULATED_UNTRUSTED
    _SIMULATED_UNAVAILABLE = unavailable or []
    _SIMULATED_UNTRUSTED = untrusted or []

async def get_available_local_models(force_refresh: bool = False) -> List[str]:
    """Queries Ollama for currently installed local models with short TTL caching."""
    global _installed_cache, _installed_cache_time
    now = time.time()
    if not force_refresh and _installed_cache and (now - _installed_cache_time < 5.0):
        return [m for m in _installed_cache if m not in _SIMULATED_UNAVAILABLE]

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                _installed_cache = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                _installed_cache_time = now
                return [m for m in _installed_cache if m not in _SIMULATED_UNAVAILABLE]
    except Exception:
        pass
    
    # Fallback default expected on-prem set if Ollama query fails
    return [m for m in ["qwen2.5:3b", "qwen2.5:7b-instruct", "qwen2.5vl:7b", "qwen2.5-coder:3b", "deepseek-r1:1.5b"] if m not in _SIMULATED_UNAVAILABLE]

def log_fallback_event(
    role: str,
    primary_model: str,
    substituted_model: str,
    reason: str
) -> Dict[str, Any]:
    """Persists transparent audit log of model fallback substitutions."""
    os.makedirs(os.path.dirname(FALLBACK_LOG_PATH), exist_ok=True)
    record = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "primary_model": primary_model,
        "substituted_model": substituted_model,
        "fallback_reason": reason
    }
    try:
        with open(FALLBACK_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print(f"[ModelRegistry Warning] Could not persist fallback log: {e}")
    return record

def get_fallback_audit_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieves recent model fallback substitution events."""
    if not os.path.exists(FALLBACK_LOG_PATH):
        return []
    records = []
    try:
        with open(FALLBACK_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line.strip()))
    except Exception:
        pass
    return records[-limit:]

async def resolve_model_for_role(
    role: str,
    preferred_model: Optional[str] = None,
    timeout_override: Optional[float] = None
) -> ModelResolution:
    """
    Core ModelRegistry entry point:
    1. Looks up ranked candidate models for the requested functional role.
    2. Validates supply chain trust status via model_integrity.py.
    3. Verifies local presence in Ollama.
    4. Cascades through fallback ranks until a usable model is found.
    5. Logs fallback event if primary candidate was substituted.
    """
    role_key = role.lower()
    candidates = ROLE_MODEL_PREFERENCES.get(role_key, ["qwen2.5:3b", "qwen2.5:7b-instruct"])
    if preferred_model and preferred_model not in candidates:
        candidates = [preferred_model] + candidates

    primary_model = candidates[0]
    timeout_sec = timeout_override or ROLE_TIMEOUTS.get(role_key, 60.0)

    installed_models = await get_available_local_models()

    selected_model: Optional[str] = None
    substitution_reason: Optional[str] = None

    for candidate in candidates:
        # Check simulated test dropouts
        if candidate in _SIMULATED_UNAVAILABLE:
            continue
        if candidate in _SIMULATED_UNTRUSTED:
            continue

        # Check supply-chain trust status
        if not is_model_trusted(candidate):
            if candidate == primary_model:
                substitution_reason = f"Primary model '{candidate}' failed cryptographic integrity check (tampered/untrusted)"
            continue

        # Check installation in Ollama
        candidate_base = candidate.split(":")[0]
        is_installed = any(candidate == inst or candidate_base == inst.split(":")[0] for inst in installed_models)
        if not is_installed and installed_models:
            if candidate == primary_model and not substitution_reason:
                substitution_reason = f"Primary model '{candidate}' not loaded/installed in local Ollama instance"
            continue

        selected_model = candidate
        break

    # If no candidate in tier passed, use absolute safe warm default
    if not selected_model:
        selected_model = "qwen2.5:3b"
        if not substitution_reason:
            substitution_reason = f"All candidate models for role '{role}' failed; defaulted to base sovereign model"

    is_fallback = (selected_model != primary_model)

    if is_fallback:
        log_fallback_event(
            role=role_key,
            primary_model=primary_model,
            substituted_model=selected_model,
            reason=substitution_reason or "Fallback candidate selected from ranked registry tier"
        )

    is_warm = selected_model in WARM_TIER_MODELS

    return ModelResolution(
        model_name=selected_model,
        role=role_key,
        is_fallback=is_fallback,
        primary_model=primary_model,
        reason=substitution_reason or f"Role '{role_key}' successfully bound to primary model '{selected_model}'",
        timeout_seconds=timeout_sec,
        is_warm=is_warm
    )

async def unload_model_from_memory(model_name: str) -> bool:
    """
    Tiered memory management: unloads an idle large model from VRAM
    by sending a keep_alive: 0 request to Ollama.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": model_name, "prompt": "", "keep_alive": 0}
            )
            return resp.status_code == 200
    except Exception:
        return False
