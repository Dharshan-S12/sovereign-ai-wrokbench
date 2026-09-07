"""
MRPL Sovereign Workbench — Master Benchmark Evaluation Suite
Evaluates the sovereign pipeline across the held-out test split of refinery_scenarios_v1.jsonl.
Computes and reports Precision, Recall, and F1-score across:
1. Intent Routing
2. Parameter Extraction
3. Rule Engine ISO Zone & Verdict Evaluation
4. Action Recommendation Determination

Saves dated report artifact to backend/benchmarks/results/YYYY-MM-DD_report.json.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
from collections import defaultdict

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND_DIR)

from app.router.task_router import auto_detect_task_intent, get_router_tier_metrics, reset_router_tier_metrics
from app.rules.rule_engine import evaluate_rules, normalize_extractor_output

BENCHMARK_PATH = os.path.join(BACKEND_DIR, "benchmarks", "refinery_scenarios_v1.jsonl")
RESULTS_DIR = os.path.join(BACKEND_DIR, "benchmarks", "results")

def calculate_metrics(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    precision = (tp / (tp + fp)) * 100.0 if (tp + fp) > 0 else 100.0
    recall = (tp / (tp + fn)) * 100.0 if (tp + fn) > 0 else 100.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 100.0
    return round(precision, 2), round(recall, 2), round(f1, 2)

def determine_action_recommendation(rule_eval: Dict[str, Any], intent: str) -> str:
    if not rule_eval.get("evaluated"):
        if intent == "ocr":
            return "manual_verification"
        elif intent == "disambiguation":
            return "manual_verification"
        elif intent == "predictive_trend":
            return "schedule_maintenance"
        return "general_response"

    verdict = rule_eval.get("overall_verdict")
    if verdict == "NON_COMPLIANT":
        # Check if Zone D
        for r in rule_eval.get("rule_results", []):
            if "Zone D" in r.get("zone_label", ""):
                return "immediate_shutdown"
        return "schedule_maintenance"
    elif verdict == "NEEDS_REVIEW":
        return "restricted_operation"
    elif verdict == "COMPLIANT":
        return "routine_monitoring"
    return "general_response"

def run_benchmark():
    print("================================================================================")
    print("  ITEM 4: HELD-OUT REFINERY SCENARIO BENCHMARK SUITE (V1)")
    print("================================================================================\n")

    if not os.path.exists(BENCHMARK_PATH):
        print(f"[ERROR] Benchmark dataset missing at {BENCHMARK_PATH}")
        sys.exit(1)

    all_scenarios = []
    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                all_scenarios.append(json.loads(line.strip()))

    test_scenarios = [s for s in all_scenarios if s.get("split") == "test"]
    print(f"Loaded {len(all_scenarios)} total scenarios. Evaluating on {len(test_scenarios)} held-out TEST scenarios.\n")

    # Metrics Trackers: category -> {"tp": int, "fp": int, "fn": int}
    routing_stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    extraction_correct = 0
    extraction_total = 0
    rule_zone_correct = 0
    rule_verdict_correct = 0
    rule_total = 0
    action_stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    reset_router_tier_metrics()
    start_time = time.perf_counter()

    for scen in test_scenarios:
        prompt = scen["prompt"]
        exp_intent = scen["expected_intent"]
        exp_vals = scen.get("expected_extracted_values", {})
        exp_zone = scen.get("expected_zone")
        exp_verdict = scen.get("expected_rule_verdict")
        exp_action = scen.get("expected_action_category")

        # 1. Routing
        route_res = auto_detect_task_intent(prompt)
        act_intent = "disambiguation" if route_res.is_ambiguous else route_res.task_type
        
        # Normalize rule_check / doc_gen equivalency for compliance queries
        is_intent_correct = (act_intent == exp_intent) or (exp_intent == "rule_check" and act_intent in ["rule_check", "doc_gen"]) or (exp_intent == "doc_gen" and act_intent in ["doc_gen", "rule_check"])
        
        if is_intent_correct:
            routing_stats[exp_intent]["tp"] += 1
        else:
            routing_stats[exp_intent]["fn"] += 1
            routing_stats[act_intent]["fp"] += 1

        # 2. Extraction & Rule Engine Evaluation
        extracted = normalize_extractor_output(raw_extracted={}, raw_context=prompt, input_prompt=prompt)
        
        # Check extraction
        if exp_vals:
            extraction_total += 1
            all_fields_matched = True
            for k, expected_v in exp_vals.items():
                act_v = extracted.get(k)
                if isinstance(expected_v, float):
                    if act_v is None or abs(float(act_v) - expected_v) > 0.05:
                        all_fields_matched = False
                        break
                elif isinstance(expected_v, str):
                    if act_v is None or str(act_v).upper() != expected_v.upper():
                        all_fields_matched = False
                        break
            if all_fields_matched:
                extraction_correct += 1

        # Check rule engine
        rule_eval = evaluate_rules(extracted, raw_context=prompt)
        if exp_verdict:
            rule_total += 1
            # Check verdict
            if rule_eval.get("overall_verdict") == exp_verdict:
                rule_verdict_correct += 1
            
            # Check zone
            if exp_zone:
                actual_zone = None
                for r in rule_eval.get("rule_results", []):
                    if "Zone A" in r.get("zone_label", ""):
                        actual_zone = "Zone A"
                    elif "Zone B" in r.get("zone_label", ""):
                        actual_zone = "Zone B"
                    elif "Zone C" in r.get("zone_label", ""):
                        actual_zone = "Zone C"
                    elif "Zone D" in r.get("zone_label", ""):
                        actual_zone = "Zone D"
                if actual_zone == exp_zone:
                    rule_zone_correct += 1

        # 3. Action Recommendation
        act_action = determine_action_recommendation(rule_eval, act_intent)
        if exp_action:
            if act_action == exp_action:
                action_stats[exp_action]["tp"] += 1
            else:
                action_stats[exp_action]["fn"] += 1
                action_stats[act_action]["fp"] += 1

    duration = time.perf_counter() - start_time

    # Compute Summary Metrics
    print("--------------------------------------------------------------------------------")
    print("1. INTENT ROUTING PERFORMANCE (HELD-OUT TEST SET)")
    print("--------------------------------------------------------------------------------")
    print(f"{'Intent Class':<20} | {'Precision':<10} | {'Recall':<10} | {'F1-Score':<10}")
    print("-" * 58)
    routing_metrics = {}
    for cls_name in sorted(routing_stats.keys()):
        s = routing_stats[cls_name]
        p, r, f1 = calculate_metrics(s["tp"], s["fp"], s["fn"])
        routing_metrics[cls_name] = {"precision": p, "recall": r, "f1": f1}
        print(f"{cls_name:<20} | {p:>8.1f}% | {r:>8.1f}% | {f1:>8.1f}%")

    print("\n--------------------------------------------------------------------------------")
    print("2. EXTRACTION & RULE ENGINE PERFORMANCE")
    print("--------------------------------------------------------------------------------")
    ext_pct = (extraction_correct / max(1, extraction_total)) * 100.0
    zone_pct = (rule_zone_correct / max(1, rule_total)) * 100.0
    verdict_pct = (rule_verdict_correct / max(1, rule_total)) * 100.0
    print(f"Numerical Parameter Extraction Accuracy : {extraction_correct}/{extraction_total} ({ext_pct:.1f}%)")
    print(f"ISO 10816-3 Zone Classification Accuracy : {rule_zone_correct}/{rule_total} ({zone_pct:.1f}%)")
    print(f"Authoritative Rule Verdict Accuracy     : {rule_verdict_correct}/{rule_total} ({verdict_pct:.1f}%)")

    print("\n--------------------------------------------------------------------------------")
    print("3. ACTION RECOMMENDATION PERFORMANCE")
    print("--------------------------------------------------------------------------------")
    print(f"{'Action Category':<25} | {'Precision':<10} | {'Recall':<10} | {'F1-Score':<10}")
    print("-" * 63)
    action_metrics = {}
    for act_name in sorted(action_stats.keys()):
        s = action_stats[act_name]
        p, r, f1 = calculate_metrics(s["tp"], s["fp"], s["fn"])
        action_metrics[act_name] = {"precision": p, "recall": r, "f1": f1}
        print(f"{act_name:<25} | {p:>8.1f}% | {r:>8.1f}% | {f1:>8.1f}%")

    # Three-Tier Router Performance Breakdown
    tier_metrics = get_router_tier_metrics()
    print("\n--------------------------------------------------------------------------------")
    print("4. ROUTER THREE-TIER USAGE BREAKDOWN")
    print("--------------------------------------------------------------------------------")
    tot = max(1, tier_metrics["total_queries"])
    print(f"Tier 1 (Fast Deterministic Classifier) : {tier_metrics['tier_1_count']:>3}/{tot} ({tier_metrics['tier_1_resolution_rate']:>6.1%})")
    print(f"Tier 2 (LLM Semantic Fallback)         : {tier_metrics['tier_2_count']:>3}/{tot} ({tier_metrics['tier_2_fallback_rate']:>6.1%})")
    print(f"Tier 3 (Disambiguation UI)             : {tier_metrics['tier_3_count']:>3}/{tot} ({tier_metrics['tier_3_disambiguation_rate']:>6.1%})")

    # Save dated report artifact
    os.makedirs(RESULTS_DIR, exist_ok=True)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_file = os.path.join(RESULTS_DIR, f"{today_str}_report.json")

    report_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark_dataset": "refinery_scenarios_v1.jsonl",
        "split_evaluated": "test",
        "scenarios_count": len(test_scenarios),
        "duration_seconds": round(duration, 3),
        "metrics": {
            "intent_routing": routing_metrics,
            "router_tier_breakdown": tier_metrics,
            "extraction_accuracy_pct": round(ext_pct, 2),
            "rule_zone_accuracy_pct": round(zone_pct, 2),
            "rule_verdict_accuracy_pct": round(verdict_pct, 2),
            "action_recommendation": action_metrics
        }
    }

    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    print("\n--------------------------------------------------------------------------------")
    print(f"[REPORT PERSISTED] Dated benchmark artifact written to:\n  {report_file}")
    print("================================================================================\n")

    assert ext_pct >= 90.0, f"Extraction accuracy {ext_pct:.1f}% below 90.0%"
    assert verdict_pct >= 95.0, f"Rule verdict accuracy {verdict_pct:.1f}% below 95.0%"

    print("[SUCCESS] All benchmark evaluation standards met across held-out test scenarios!")

if __name__ == "__main__":
    run_benchmark()
