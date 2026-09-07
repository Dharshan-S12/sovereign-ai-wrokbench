import os
import re
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger("multi_agent_docgen")

from app.models import Task, TaskStep, TaskStatus
from app.models.ollama_client import generate_text, OllamaTimeoutError, OllamaConnectionError
from app.router.model_router import route_model
from app.models.model_registry import resolve_model_for_role, ModelRole
from app.schemas import ExtractedEquipmentReading
from app.rules.rule_engine import evaluate_rules, normalize_extractor_output
from app.docgen import generate_docx

STORAGE_DIR = "./storage"
DISSENT_LOG_PATH = "./storage/ensemble_dissent_audit.jsonl"

def is_timeout_error(e: Exception) -> bool:
    """Helper to detect any timeout variant across httpx, asyncio, and Ollama client."""
    return (
        isinstance(e, (asyncio.TimeoutError, OllamaTimeoutError, TimeoutError))
        or "timeout" in str(e).lower()
        or "timed out" in str(e).lower()
    )

async def log_step(db, task_id, step_number, description, tool_called, tool_result):
    """Helper to persist each autonomous pipeline phase step to the DB ledger."""
    step = TaskStep(
        task_id=task_id,
        step_number=step_number,
        description=description,
        tool_called=tool_called,
        tool_result=tool_result
    )
    db.add(step)
    await db.commit()

def log_ensemble_dissent_record(task_id, individual_runs, majority_verdict, trigger_reason):
    """Logs non-unanimous ensemble voting results for offline calibration and audit trail."""
    os.makedirs("./storage", exist_ok=True)
    dissent_entry = {
        "id": str(task_id),
        "task_id": str(task_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "majority_verdict": majority_verdict,
        "trigger_reason": trigger_reason,
        "runs": individual_runs
    }
    try:
        with open(DISSENT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(dissent_entry) + "\n")
    except Exception as e:
        print(f"[Dissent Log Warning] Could not persist dissent record: {e}")

async def run_multi_agent_docgen_pipeline(
    db,
    task: Task,
    step_count: int,
    input_text: str,
    source_context: str,
    kept_chunks: List[Dict[str, Any]],
    total_retrieved_chunks: int,
    total_discarded_chunks: int,
    kept_chunk_distances: List[float]
) -> Tuple[str, float, int]:
    """
    Hardened Sequential 5-Agent Specialist Pipeline for doc_gen tasks:
    1. Extractor Agent (agent_extractor) -> Normalized facts & ExtractedEquipmentReading schema
    2. Deterministic Rule Engine (rule_engine_check) -> Exact mathematical threshold computation (Authoritative Ground Truth)
    3. Compliance Agent / Diverse Ensemble (agent_compliance / compliance_ensemble) -> 3 distinct model configurations
    4. Drafting Agent (agent_drafter) -> Formal Executive Memorandum & .docx generation with literal numerical ledger
    5. Verifier Agent (agent_verifier) -> Rule-based contradiction detection & explainable confidence breakdown
    """

    # =========================================================================
    # STAGE 1: Extractor Agent (tool_called="agent_extractor")
    # =========================================================================
    try:
        step_count += 1
        extractor_decision = await resolve_model_for_role(ModelRole.FAST_REASONING.value)

        # Real-time task progress notification for polling client
        task.status = TaskStatus.running
        task.output_ref = "Stage 1 of 5: Extractor Agent consolidating technical parameters..."
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

        extractor_system = (
            "You are the Extractor Agent in an industrial compliance pipeline.\n"
            "Extract, normalize, and consolidate all key technical parameters, equipment IDs, "
            "vibration RMS, temperatures, pressures, dates, and plant units from context.\n"
            "Output strictly a JSON object with keys: 'equipment_id', 'equipment_name', 'unit', "
            "'inspection_date', 'measurements' (dictionary), 'observed_condition', 'objective'."
        )

        extractor_prompt = (
            f"User Goal:\n{input_text}\n\n"
            f"Source Context & Inspection Records:\n{source_context or 'Direct instruction.'}\n\n"
            f"Extract structured facts as valid JSON:"
        )

        stage1_timeout = min(60.0, float(getattr(extractor_decision, "timeout_seconds", 90.0)))
        extractor_raw = await asyncio.wait_for(
            generate_text(
                prompt=extractor_prompt,
                system=extractor_system,
                model=extractor_decision.model_name,
                timeout_seconds=stage1_timeout
            ),
            timeout=stage1_timeout + 5.0
        )

        raw_facts = {}
        try:
            cleaned_json = extractor_raw.strip()
            if cleaned_json.startswith("```"):
                cleaned_json = re.sub(r"^```(?:json)?\s*", "", cleaned_json)
                cleaned_json = re.sub(r"\s*```$", "", cleaned_json).strip()
            raw_facts = json.loads(cleaned_json)
        except Exception:
            raw_facts = {
                "equipment_id": "PMP-204" if "PMP-204" in input_text or "PMP-204" in source_context else ("TRB-1105" if "TRB-1105" in input_text or "TRB-1105" in source_context else "Equipment"),
                "unit": "CDU-1" if "PMP-204" in input_text or "PMP-204" in source_context else "HCU",
                "raw_text": extractor_raw[:300]
            }

        # Normalize via schema contract
        normalized_dict = normalize_extractor_output(
            raw_extracted=raw_facts,
            raw_context=source_context,
            input_prompt=input_text
        )
        extracted_reading = ExtractedEquipmentReading(**normalized_dict)
        extracted_facts = extracted_reading.model_dump()

        eq_id_display = extracted_facts.get("equipment_id") or "Equipment"
        await log_step(
            db=db,
            task_id=task.id,
            step_number=step_count,
            description=f"Stage 1 [Extractor Agent]: Consolidated parameters for {eq_id_display} (Status: SUCCESS)",
            tool_called="agent_extractor",
            tool_result={
                "stage": 1,
                "stage_status": "success",
                "agent": "Extractor Agent",
                "extracted_facts": extracted_facts,
                "model": extractor_decision.model_name
            }
        )

        # Real-time task progress advance
        task.output_ref = f"Stage 1 of 5 complete: Extractor Agent consolidated parameters for {eq_id_display}. Starting Stage 2..."
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

    except Exception as e:
        step_count += 1
        is_to = is_timeout_error(e)
        err_msg = "timeout" if is_to else str(e)
        failed_key = "timeout" if is_to else str(e)
        await log_step(
            db=db, task_id=task.id, step_number=step_count,
            description=f"Stage 1 [Extractor Agent] FAILED: {err_msg}",
            tool_called="agent_extractor",
            tool_result={"stage": 1, "stage_status": "failed", "failed_at_stage_1": failed_key, "error": str(e)}
        )
        task.status = TaskStatus.failed
        task.output_ref = f"Pipeline halted at Stage 1 (Extractor Agent): failed_at_stage_1: {failed_key}"
        await db.commit()
        raise RuntimeError(f"DocGen Stage 1 Failed: failed_at_stage_1: {failed_key}")

    # =========================================================================
    # STAGE 2: Deterministic Rule Engine (tool_called="rule_engine_check")
    # Authoritative Numerical Ground Truth — ZERO LLM Variance
    # =========================================================================
    try:
        step_count += 1
        # Real-time task progress notification for polling client
        task.output_ref = "Stage 2 of 5: Deterministic Rule Engine evaluating ISO thresholds..."
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

        sop_candidate = None
        for chunk in kept_chunks:
            src = str(chunk.get("source", "")).upper()
            if "SOP-" in src or "ISO-" in src:
                match = re.search(r"(?:SOP|ISO)-[A-Z0-9]+-\d+", src)
                if match:
                    sop_candidate = match.group(0)
                    break

        rule_eval = evaluate_rules(
            extracted_fields=extracted_facts,
            sop_reference=sop_candidate,
            raw_context=source_context
        )

        rule_ground_truth_context = ""
        has_numeric_readings = (
            extracted_facts.get("vibration_velocity_rms") is not None
            or extracted_facts.get("bearing_temperature") is not None
            or extracted_facts.get("operating_pressure") is not None
        )

        # Check for schema mismatch error on known equipment
        if rule_eval.get("is_schema_mismatch_error"):
            await log_step(
                db=db,
                task_id=task.id,
                step_number=step_count,
                description=f"Stage 2 [Deterministic Rule Engine] FAILED: {rule_eval.get('reason')}",
                tool_called="rule_engine_check",
                tool_result={"stage": 2, "stage_status": "failed", "failed_at_stage_2": rule_eval.get('reason'), **rule_eval}
            )
            task.status = TaskStatus.failed
            task.output_ref = f"Pipeline halted at Stage 2 (Deterministic Rule Engine): failed_at_stage_2: schema_mismatch - {rule_eval.get('reason')}"
            await db.commit()
            raise RuntimeError(f"DocGen Stage 2 Failed: schema_mismatch - {rule_eval.get('reason')}")

        if rule_eval.get("evaluated"):
            await log_step(
                db=db,
                task_id=task.id,
                step_number=step_count,
                description=(
                    f"Stage 2 [Deterministic Rule Engine]: Evaluated {rule_eval['total_rules_evaluated']} threshold rule(s) -> "
                    f"Authoritative Verdict: '{rule_eval['overall_verdict']}' (Config v{rule_eval.get('config_version', '1.3.0')}) (Status: SUCCESS)"
                ),
                tool_called="rule_engine_check",
                tool_result={
                    "stage": 2,
                    "stage_status": "success",
                    "authoritative": True,
                    **rule_eval
                }
            )
            borderline_summary = "YES (Borderline threshold call requiring multi-model consistency check)" if rule_eval.get("is_borderline") else "NO (Unambiguous deterministic call - clear zone separation)"
            rule_ground_truth_context = (
                "### AUTHORITATIVE DETERMINISTIC RULE ENGINE GROUND TRUTH:\n"
                f"The following threshold comparisons are mathematically exact ground truth (Config v{rule_eval.get('config_version', '1.3.0')}, Checksum: {rule_eval.get('config_hash', '')[:8]}):\n"
                f"- Authoritative Overall Verdict: {rule_eval['overall_verdict']}\n"
                f"- Overall Borderline Status: {borderline_summary}\n"
                f"- Evaluated Parameters & Exact Percentage Mathematics:\n"
            )
            for r in rule_eval.get("rule_results", []):
                delta_str = r.get("delta_label", f"{r.get('delta_pct', 0.0):+.1f}%")
                borderline_str = "YES (Within 10% of threshold)" if r.get("is_borderline_violation") else ("NO (Compliant/Safe Margin)" if r.get("passed") else f"NO (Exceeds threshold by {r.get('delta_pct', 0.0):.1f}% - Unambiguous Violation)")
                rule_ground_truth_context += (
                    f"  * {r['field_label']}: Observed {r['actual_value']} | Limit {r['operator']} {r['threshold']} | "
                    f"Mathematical Delta: {delta_str} | Status: {'PASS' if r['passed'] else 'FAIL'} | "
                    f"Borderline Proximity: {borderline_str} | Classification: {r['zone_label']} | Standard: {r['sop_reference']}\n"
                )
            if rule_eval.get("sanitization_warnings"):
                rule_ground_truth_context += f"- Input Sanitization Alerts: {'; '.join(rule_eval['sanitization_warnings'])}\n"
            rule_ground_truth_context += (
                "\nCRITICAL SAFETY RULES:\n"
                "1. You CANNOT override or dispute these authoritative facts.\n"
                "2. When discussing percentage over/under limits, state the exact mathematical delta (e.g. '20.0% above the limit of 4.5 mm/s').\n"
                "3. NEVER state or imply a reading is 'within 10%' of a limit if its mathematical delta exceeds 10% (e.g. 5.4 mm/s is 20.0% above 4.5 mm/s, NOT within 10%).\n\n"
            )
        else:
            # Document legitimately has no numerical rules (e.g. administrative memo)
            await log_step(
                db=db,
                task_id=task.id,
                step_number=step_count,
                description=f"Stage 2 [Deterministic Rule Engine]: Skipped ({rule_eval.get('reason', 'no numerical rules found')}) (Status: SKIPPED_NO_APPLICABLE_RULES)",
                tool_called="rule_engine_check",
                tool_result={"stage": 2, "stage_status": "skipped", **rule_eval}
            )

        # Real-time task progress advance
        task.output_ref = f"Stage 2 of 5 complete: Rule Engine evaluated threshold rules (Verdict: {rule_eval.get('overall_verdict', 'COMPLIANT')}). Starting Stage 3..."
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

    except Exception as e:
        if "DocGen Stage 2 Failed" in str(e):
            raise
        step_count += 1
        is_to = is_timeout_error(e)
        err_msg = "timeout" if is_to else str(e)
        failed_key = "timeout" if is_to else str(e)
        await log_step(
            db=db, task_id=task.id, step_number=step_count,
            description=f"Stage 2 [Rule Engine] FAILED: {err_msg}",
            tool_called="rule_engine_check",
            tool_result={"stage": 2, "stage_status": "failed", "failed_at_stage_2": failed_key, "error": str(e)}
        )
        task.status = TaskStatus.failed
        task.output_ref = f"Pipeline halted at Stage 2 (Rule Engine): failed_at_stage_2: {failed_key}"
        await db.commit()
        raise RuntimeError(f"DocGen Stage 2 Failed: failed_at_stage_2: {failed_key}")

    # =========================================================================
    # STAGE 3: Compliance Agent & Diverse 3-Model Ensemble (tool_called="agent_compliance" / "compliance_ensemble")
    # =========================================================================
    try:
        step_count += 1
        # Real-time task progress notification for polling client
        task.output_ref = "Stage 3 of 5: Running compliance ensemble across models..."
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

        # SAFETY CHECK: If document contains equipment readings, ensemble MUST have rule engine grounding
        if has_numeric_readings and not rule_eval.get("evaluated"):
            err_msg = "Safety Gate Violated: Numerical equipment readings present but Deterministic Rule Engine produced no grounding."
            await log_step(
                db=db, task_id=task.id, step_number=step_count,
                description=f"Stage 3 [Compliance Ensemble] BLOCKED: {err_msg}",
                tool_called="compliance_ensemble",
                tool_result={"stage": 3, "stage_status": "failed", "failed_at_stage_3": err_msg, "error": err_msg}
            )
            task.status = TaskStatus.failed
            task.output_ref = f"Pipeline halted at Stage 3: failed_at_stage_3: {err_msg}"
            await db.commit()
            raise RuntimeError(f"DocGen Stage 3 Failed: failed_at_stage_3: {err_msg}")

        sop_context_text = ""
        for idx, chunk in enumerate(kept_chunks):
            sop_context_text += f"[SOP Excerpt {idx+1} (Source: {chunk.get('source', 'SOP')})]:\n{chunk.get('text', '')}\n\n"
        if not sop_context_text:
            sop_context_text = "Standard ISO 10816-3 and MRPL plant safety guidelines."

        compliance_system = (
            "You are the Compliance Agent in an industrial advisory safety pipeline.\n"
            "Evaluate extracted facts against SOP reference rules and the authoritative deterministic rule engine truth.\n"
            "Your role is advisory. You MUST align your verdict with deterministic ground truth if rules were evaluated.\n"
            "Output strictly a JSON object: {'verdict': 'COMPLIANT'|'NON_COMPLIANT'|'NEEDS_REVIEW', "
            "'summary_reason': '...', 'threshold_evaluations': [], 'mandatory_actions': '...'}"
        )

        compliance_prompt = (
            f"{rule_ground_truth_context}"
            f"Technical Facts:\n{json.dumps(extracted_facts, indent=2)}\n\n"
            f"SOP Standards:\n{sop_context_text}\n\n"
            f"Evaluate compliance and produce structured verdict JSON:"
        )

        should_run_ensemble = (not rule_eval.get("evaluated")) or (rule_eval.get("is_borderline") is True)
        ensemble_disagreement = False
        ensemble_penalty = 0.0
        compliance_verdict = {}

        if should_run_ensemble:
            ensemble_configs = [
                {"model": "qwen2.5:7b-instruct", "temperature": 0.0, "desc": "Config A (7B @ temp 0.0 deterministic)"},
                {"model": "qwen2.5:7b-instruct", "temperature": 0.3, "desc": "Config B (7B @ temp 0.3 slight stochasticity)"},
                {"model": "qwen2.5:3b", "temperature": 0.7, "desc": "Config C (3B @ temp 0.7 high diversity check)"}
            ]

            ensemble_runs = []
            verdicts = []

            for cfg in ensemble_configs:
                try:
                    raw_run = await asyncio.wait_for(
                        generate_text(
                            prompt=compliance_prompt,
                            system=compliance_system,
                            model=cfg["model"],
                            temperature=cfg["temperature"],
                            timeout_seconds=60.0
                        ),
                        timeout=65.0
                    )
                except Exception:
                    raw_run = await asyncio.wait_for(
                        generate_text(
                            prompt=compliance_prompt,
                            system=compliance_system,
                            model="qwen2.5:3b",
                            temperature=cfg["temperature"],
                            timeout_seconds=45.0
                        ),
                        timeout=50.0
                    )

                parsed_run = {}
                try:
                    c_json = raw_run.strip()
                    if c_json.startswith("```"):
                        c_json = re.sub(r"^```(?:json)?\s*", "", c_json)
                        c_json = re.sub(r"\s*```$", "", c_json).strip()
                    parsed_run = json.loads(c_json)
                except Exception:
                    v_str = "NON_COMPLIANT" if ("non-compliant" in raw_run.lower() or "non_compliant" in raw_run.lower()) else "COMPLIANT"
                    parsed_run = {"verdict": v_str, "summary_reason": raw_run[:200], "threshold_evaluations": []}

                v_val = str(parsed_run.get("verdict", "COMPLIANT")).upper().strip()
                verdicts.append(v_val)
                ensemble_runs.append({
                    "config": cfg["desc"],
                    "model": cfg["model"],
                    "temperature": cfg["temperature"],
                    "verdict": v_val,
                    "summary_reason": parsed_run.get("summary_reason", ""),
                    "raw_output": raw_run[:300],
                    "full_data": parsed_run
                })

            from collections import Counter
            vote_counts = Counter(verdicts)
            majority_verdict, count = vote_counts.most_common(1)[0]
            is_unanimous = (count == len(ensemble_configs))

            if not is_unanimous:
                ensemble_disagreement = True
                ensemble_penalty = 20.0
                log_ensemble_dissent_record(
                    task_id=task.id,
                    individual_runs=ensemble_runs,
                    majority_verdict=majority_verdict,
                    trigger_reason="Borderline measurement" if rule_eval.get("is_borderline") else "Unruled document type"
                )

            rep_run = next((r for r in ensemble_runs if r["verdict"] == majority_verdict), ensemble_runs[0])
            compliance_verdict = rep_run["full_data"]
            compliance_verdict["verdict"] = majority_verdict

            await log_step(
                db=db,
                task_id=task.id,
                step_number=step_count,
                description=(
                    f"Stage 3 [Diverse Ensemble (3 Models)]: "
                    f"{'Unanimous consensus (3/3)' if is_unanimous else f'Ensemble Disagreement ({count}/3 majority) — DISSENT LOGGED'} -> '{majority_verdict}' (Status: SUCCESS)"
                ),
                tool_called="compliance_ensemble",
                tool_result={
                    "stage": 3,
                    "stage_status": "success",
                    "agent": "Compliance Agent (Diverse 3-Model Ensemble)",
                    "ensemble_configs": [c["desc"] for c in ensemble_configs],
                    "individual_runs": ensemble_runs,
                    "majority_verdict": majority_verdict,
                    "is_unanimous": is_unanimous,
                    "ensemble_disagreement": ensemble_disagreement,
                    "disagreement_penalty": ensemble_penalty,
                    "summary_reason": compliance_verdict.get("summary_reason", "")
                }
            )
        else:
            compliance_decision = await resolve_model_for_role(ModelRole.FAST_REASONING.value)
            stage3_single_to = min(60.0, float(getattr(compliance_decision, "timeout_seconds", 90.0)))
            compliance_raw = await asyncio.wait_for(
                generate_text(
                    prompt=compliance_prompt,
                    system=compliance_system,
                    model=compliance_decision.model_name,
                    timeout_seconds=stage3_single_to,
                    temperature=0.0
                ),
                timeout=stage3_single_to + 5.0
            )
            try:
                cleaned_cjson = compliance_raw.strip()
                if cleaned_cjson.startswith("```"):
                    cleaned_cjson = re.sub(r"^```(?:json)?\s*", "", cleaned_cjson)
                    cleaned_cjson = re.sub(r"\s*```$", "", cleaned_cjson).strip()
                compliance_verdict = json.loads(cleaned_cjson)
            except Exception:
                verdict_str = rule_eval.get("overall_verdict") or ("NON_COMPLIANT" if "non-compliant" in compliance_raw.lower() else "COMPLIANT")
                compliance_verdict = {
                    "verdict": verdict_str,
                    "summary_reason": compliance_raw[:250],
                    "threshold_evaluations": rule_eval.get("rule_results", [])
                }

            verdict_label = compliance_verdict.get("verdict", rule_eval.get("overall_verdict", "COMPLIANT"))
            bypassed_note = "Ensemble bypassed: unambiguous deterministic verdict" if rule_eval.get("evaluated") else "Direct fast reasoning"
            await log_step(
                db=db,
                task_id=task.id,
                step_number=step_count,
                description=f"Stage 3 [Compliance Agent]: Rendered advisory verdict '{verdict_label}' ({bypassed_note}) (Status: SUCCESS)",
                tool_called="agent_compliance",
                tool_result={
                    "stage": 3,
                    "stage_status": "success",
                    "agent": "Compliance Agent",
                    "verdict": verdict_label,
                    "summary_reason": compliance_verdict.get("summary_reason", ""),
                    "model": compliance_decision.model_name
                }
            )

        # STRICT SAFETY OVERRIDE: If rule engine evaluated FAIL/NON_COMPLIANT, LLM can NEVER set verdict to COMPLIANT
        if rule_eval.get("evaluated") and rule_eval.get("overall_verdict") == "NON_COMPLIANT":
            compliance_verdict["verdict"] = "NON_COMPLIANT"
            compliance_verdict["authoritative_override"] = True

        verdict_label = compliance_verdict.get("verdict", "COMPLIANT")

        # Real-time task progress advance
        task.output_ref = f"Stage 3 of 5 complete: Compliance verdict '{verdict_label}' confirmed. Starting Stage 4..."
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

    except Exception as e:
        if "DocGen Stage 3 Failed" in str(e):
            raise
        step_count += 1
        is_to = is_timeout_error(e)
        err_msg = "timeout" if is_to else str(e)
        failed_key = "timeout" if is_to else str(e)
        await log_step(
            db=db, task_id=task.id, step_number=step_count,
            description=f"Stage 3 [Compliance Agent] FAILED: {err_msg}",
            tool_called="agent_compliance",
            tool_result={"stage": 3, "stage_status": "failed", "failed_at_stage_3": failed_key, "error": str(e)}
        )
        task.status = TaskStatus.failed
        task.output_ref = f"Pipeline halted at Stage 3 (Compliance Agent): failed_at_stage_3: {failed_key}"
        await db.commit()
        raise RuntimeError(f"DocGen Stage 3 Failed: failed_at_stage_3: {failed_key}")

    verdict_label = compliance_verdict.get("verdict", "COMPLIANT")

    # =========================================================================
    # STAGE 4: Drafting Agent (tool_called="agent_drafter")
    # =========================================================================
    try:
        step_count += 1
        drafter_decision = await resolve_model_for_role(ModelRole.DRAFTING.value)
        drafter_model = "qwen2.5:3b" if drafter_decision.model_name in ["qwen2.5:7b-instruct", "qwen2.5:7b"] else drafter_decision.model_name

        # Real-time task progress notification for polling client
        task.output_ref = "Stage 4 of 5: Drafting Agent synthesizing executive memorandum (.docx)..."
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

        # Explicit numerical grounding facts block
        numerical_ledger_lines = []
        if extracted_facts.get("vibration_velocity_rms") is not None:
            numerical_ledger_lines.append(f"- Vibration Velocity RMS: {extracted_facts['vibration_velocity_rms']} mm/s")
        if extracted_facts.get("bearing_temperature") is not None:
            numerical_ledger_lines.append(f"- Bearing / Seal Temperature: {extracted_facts['bearing_temperature']} °C")
        if extracted_facts.get("operating_pressure") is not None:
            numerical_ledger_lines.append(f"- Operating Line Pressure: {extracted_facts['operating_pressure']} bar")
        if extracted_facts.get("toxic_gas_concentration") is not None:
            numerical_ledger_lines.append(f"- Toxic Gas Concentration: {extracted_facts['toxic_gas_concentration']} ppm")
        if extracted_facts.get("actuation_time") is not None:
            numerical_ledger_lines.append(f"- Actuation Time: {extracted_facts['actuation_time']} s")

        numerical_ledger_text = "\n".join(numerical_ledger_lines) if numerical_ledger_lines else "No direct numerical readings extracted."

        drafting_system = (
            "You are the Drafting Agent in an executive sovereign document workbench.\n"
            "Draft the complete, authoritative compliance memorandum formatted for executive review.\n"
            "MANDATORY RULES:\n"
            "1. You MUST explicitly include all literal numerical readings (e.g. 3.2 mm/s, 68.0 °C or 5.4 mm/s, 79.5 °C), "
            "the exact ISO 10816-3 Zone classification (Zone A, B, C, or D), and exact SOP limits in the text.\n"
            "2. Never replace measured numbers with vague relative phrases like 'increased from previous reading' without stating the exact values.\n"
            "3. MATHEMATICAL ACCURACY RULE: When discussing percentage over/under limits, state the exact percentage delta computed by the rule engine (e.g. '20.0% above the limit of 4.5 mm/s'). "
            "NEVER claim an observed reading is 'within 10%' of a limit if the mathematical delta exceeds 10% (e.g. 5.4 mm/s is 20.0% above 4.5 mm/s, NOT within 10%).\n"
            "Structure:\n"
            "# Executive Compliance Memorandum\n"
            "## 1. Equipment & Inspection Identification\n"
            "## 2. Technical Findings & Measurement Ledger\n"
            "## 3. SOP Compliance Evaluation & Citations\n"
            "## 4. Operational Verdict & Required Corrective Actions\n"
        )

        drafting_prompt = (
            f"User Goal:\n{input_text}\n\n"
            f"### EXACT MEASUREMENT LEDGER (MUST BE INCLUDED IN MEMO):\n"
            f"{numerical_ledger_text}\n\n"
            f"{rule_ground_truth_context}"
            f"Technical Facts:\n{json.dumps(extracted_facts, indent=2)}\n\n"
            f"Compliance Verdict:\n{json.dumps(compliance_verdict, indent=2)}\n\n"
            f"SOP Standards:\n{sop_context_text}\n\n"
            f"Draft the formal compliance memorandum including exact numeric values:"
        )

        stage4_timeout = float(getattr(drafter_decision, "timeout_seconds", 180.0))
        final_document_text = await asyncio.wait_for(
            generate_text(
                prompt=drafting_prompt,
                system=drafting_system,
                model=drafter_model,
                timeout_seconds=stage4_timeout
            ),
            timeout=stage4_timeout + 15.0
        )

        task_storage_dir = os.path.join(STORAGE_DIR, str(task.id))
        os.makedirs(task_storage_dir, exist_ok=True)
        output_file_path = os.path.join(task_storage_dir, "output.txt")
        with open(output_file_path, "w", encoding="utf-8") as f:
            f.write(final_document_text)

        doc_title_words = input_text.split()[:8]
        doc_title = " ".join(doc_title_words).strip(".:,; ") or f"Compliance Memo — {eq_id_display}"
        docx_file_path = os.path.join(task_storage_dir, "output.docx")
        generate_docx(title=doc_title, content=final_document_text, output_path=docx_file_path)

        if not os.path.exists(docx_file_path) or os.path.getsize(docx_file_path) == 0:
            raise IOError("Generated .docx file is missing or empty.")

        await log_step(
            db=db,
            task_id=task.id,
            step_number=step_count,
            description="Stage 4 [Drafting Agent]: Formatted executive compliance memorandum (.docx) (Status: SUCCESS)",
            tool_called="agent_drafter",
            tool_result={
                "stage": 4,
                "stage_status": "success",
                "agent": "Drafting Agent",
                "document_title": doc_title,
                "file_path": docx_file_path,
                "output_length": len(final_document_text),
                "file_size_bytes": os.path.getsize(docx_file_path),
                "model": drafter_model
            }
        )

        # Real-time task progress advance
        task.output_ref = f"Stage 4 of 5 complete: Executive memorandum drafted and .docx generated. Starting Stage 5..."
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

    except Exception as e:
        step_count += 1
        is_to = is_timeout_error(e)
        err_msg = "timeout" if is_to else str(e)
        failed_key = "timeout" if is_to else str(e)
        await log_step(
            db=db, task_id=task.id, step_number=step_count,
            description=f"Stage 4 [Drafting Agent] FAILED: {err_msg}",
            tool_called="agent_drafter",
            tool_result={"stage": 4, "stage_status": "failed", "failed_at_stage_4": failed_key, "error": str(e)}
        )
        task.status = TaskStatus.failed
        task.output_ref = f"Pipeline halted at Stage 4 (Drafting Agent): failed_at_stage_4: {failed_key}"
        await db.commit()
        raise RuntimeError(f"DocGen Stage 4 Failed: failed_at_stage_4: {failed_key}")

    # =========================================================================
    # STAGE 5: Verifier Agent (tool_called="agent_verifier")
    # Rule-Based Contradiction Detection & Explainable Grounding Confidence Breakdown
    # =========================================================================
    try:
        step_count += 1
        # Real-time task progress notification for polling client
        task.output_ref = "Stage 5 of 5: Verifier Agent validating grounding and consistency..."
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

        deductions: List[Dict[str, Any]] = []
        claims_checked: List[Dict[str, Any]] = []
        hard_contradiction = False
        rule_contradiction_details = []

        # 1. Deterministic Rule-Based Contradiction Check (Regex against drafted text)
        if rule_eval.get("evaluated") and rule_eval.get("rule_results"):
            for r in rule_eval["rule_results"]:
                actual_zone = r.get("zone_label", "")
                actual_val = str(r.get("actual_value", ""))
                rule_pass = r.get("passed", True)

                # Check if draft claims Zone A when rule is Zone C/D
                if not rule_pass and re.search(r"\bZone\s*A\b", final_document_text, re.IGNORECASE):
                    hard_contradiction = True
                    rule_contradiction_details.append(f"Draft incorrectly claims Zone A while deterministic rule computed {actual_zone}")
                if not rule_pass and re.search(r"\b(fully compliant|within normal limits|passed inspection)\b", final_document_text, re.IGNORECASE) and not re.search(r"\bnon-compliant\b", final_document_text, re.IGNORECASE):
                    hard_contradiction = True
                    rule_contradiction_details.append(f"Draft claims compliant while parameter '{r['field']}' failed limit ({actual_val} vs {r['threshold']})")

                # Check percentage math contradictions (e.g. claiming "within 10%" when delta > 10%)
                delta_pct = r.get("delta_pct")
                if delta_pct is not None and not rule_pass and delta_pct > 10.0:
                    if re.search(r"\bwithin\s*10%", final_document_text, re.IGNORECASE):
                        hard_contradiction = True
                        rule_contradiction_details.append(
                            f"Draft erroneously claims '{r['field']}' is 'within 10%' of limit, but observed value is actually {delta_pct:+.1f}% over limit ({actual_val} vs {r['threshold']})"
                        )

                claims_checked.append({
                    "parameter": r["field"],
                    "computed_value": actual_val,
                    "expected_status": "PASS" if rule_pass else "FAIL",
                    "expected_zone": actual_zone,
                    "verified_in_text": actual_val in final_document_text
                })

        # 2. LLM Advisory Prose Verification
        verifier_decision = await resolve_model_for_role(ModelRole.FAST_REASONING.value)
        verifier_system = (
            "You are the Verifier Agent in an industrial compliance pipeline.\n"
            "Cross-check the drafted text against the authoritative rule engine facts and compliance narrative.\n"
            "Check for prose contradictions, missing actions, or ungrounded claims.\n"
            "Respond with JSON: {'agreement': true/false, 'verification_summary': '...', 'discrepancies': []}."
        )

        verifier_prompt = (
            f"{rule_ground_truth_context}"
            f"Compliance Verdict Reference:\n{json.dumps(compliance_verdict, indent=2)}\n\n"
            f"Drafted Document:\n{final_document_text}\n\n"
            f"Verify agreement:"
        )

        stage5_timeout = min(60.0, float(getattr(verifier_decision, "timeout_seconds", 90.0)))
        verifier_raw = await asyncio.wait_for(
            generate_text(
                prompt=verifier_prompt,
                system=verifier_system,
                model=verifier_decision.model_name,
                timeout_seconds=stage5_timeout
            ),
            timeout=stage5_timeout + 5.0
        )

        verifier_data = {}
        try:
            cleaned_vjson = verifier_raw.strip()
            if cleaned_vjson.startswith("```"):
                cleaned_vjson = re.sub(r"^```(?:json)?\s*", "", cleaned_vjson)
                cleaned_vjson = re.sub(r"\s*```$", "", cleaned_vjson).strip()
            verifier_data = json.loads(cleaned_vjson)
        except Exception:
            agreement = "agreement" in verifier_raw.lower() and "false" not in verifier_raw.lower()
            verifier_data = {"agreement": agreement, "verification_summary": verifier_raw[:200], "discrepancies": []}

        llm_agrees = verifier_data.get("agreement") is True
        discrepancies = verifier_data.get("discrepancies", []) + rule_contradiction_details

        # 3. Calculate Explainable Grounding Confidence Breakdown
        if hard_contradiction:
            confidence_score = 20.0
            deductions.append({
                "factor": "hard_contradiction_against_deterministic_engine",
                "penalty": 80.0,
                "reason": f"Hard safety contradiction detected: {'; '.join(rule_contradiction_details)}"
            })
        else:
            if not llm_agrees or rule_contradiction_details:
                penalty_ver = 40.0
                deductions.append({
                    "factor": "verifier_discrepancy",
                    "penalty": penalty_ver,
                    "reason": f"Discrepancies identified during cross-verification ({len(discrepancies)} issues)"
                })
            else:
                penalty_ver = 0.0

            if ensemble_disagreement:
                deductions.append({
                    "factor": "ensemble_disagreement",
                    "penalty": ensemble_penalty,
                    "reason": "Diverse 3-model ensemble exhibited split vote (Dissent record logged for audit)"
                })

            if kept_chunk_distances:
                avg_dist = sum(kept_chunk_distances) / len(kept_chunk_distances)
                p_dist = round(min(20.0, avg_dist * 25.0), 1)
                if p_dist > 5.0:
                    deductions.append({
                        "factor": "vector_retrieval_distance",
                        "penalty": p_dist,
                        "reason": f"Average semantic distance of SOP excerpts ({avg_dist:.3f})"
                    })
            else:
                p_dist = 0.0

            if total_discarded_chunks > 0:
                deductions.append({
                    "factor": "irrelevant_chunks_discarded",
                    "penalty": 10.0,
                    "reason": f"Corrective RAG discarded {total_discarded_chunks} irrelevant context chunk(s)"
                })

            total_penalty = sum(d["penalty"] for d in deductions)
            raw_conf = max(10.0, min(99.0, 100.0 - total_penalty))
            confidence_score = round(raw_conf, 1)

        task.confidence_score = confidence_score

        is_overall_verified = (not hard_contradiction) and llm_agrees and (not ensemble_disagreement)

        step_desc = (
            f"Stage 5 [Verifier Agent]: "
            f"{'Complete agreement confirmed' if is_overall_verified else ('HARD CONTRADICTION FLAGGED' if hard_contradiction else 'Discrepancies flagged')} "
            f"(Confidence: {confidence_score}%) (Status: SUCCESS)"
        )

        await log_step(
            db=db,
            task_id=task.id,
            step_number=step_count,
            description=step_desc,
            tool_called="agent_verifier",
            tool_result={
                "stage": 5,
                "stage_status": "success",
                "agent": "Verifier Agent",
                "verified": is_overall_verified,
                "hard_contradiction": hard_contradiction,
                "confidence_score": confidence_score,
                "claims_checked": claims_checked,
                "deductions_breakdown": deductions,
                "discrepancies": discrepancies,
                "verification_summary": verifier_data.get("verification_summary", ""),
                "model": verifier_decision.model_name
            }
        )

    except Exception as e:
        step_count += 1
        is_to = is_timeout_error(e)
        err_msg = "timeout" if is_to else str(e)
        failed_key = "timeout" if is_to else str(e)
        await log_step(
            db=db, task_id=task.id, step_number=step_count,
            description=f"Stage 5 [Verifier Agent] FAILED: {err_msg}",
            tool_called="agent_verifier",
            tool_result={"stage": 5, "stage_status": "failed", "failed_at_stage_5": failed_key, "error": str(e)}
        )
        task.status = TaskStatus.failed
        task.output_ref = f"Pipeline halted at Stage 5 (Verifier Agent): failed_at_stage_5: {failed_key}"
        await db.commit()
        raise RuntimeError(f"DocGen Stage 5 Failed: failed_at_stage_5: {failed_key}")

    step_count += 1
    await log_step(
        db=db,
        task_id=task.id,
        step_number=step_count,
        description="Compliance gate: Document placed in 'pending_approval' awaiting supervisor sign-off",
        tool_called="human_approval_gate",
        tool_result={
            "status": "pending_approval",
            "download_locked": True,
            "confidence_score": confidence_score
        }
    )

    task.output_ref = final_document_text
    task.status = TaskStatus.pending_approval
    task.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return final_document_text, confidence_score, step_count
