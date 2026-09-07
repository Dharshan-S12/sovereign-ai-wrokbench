"""
MRPL Sovereign Workbench — Evaluate Full 105 Scenario Benchmark Set
Runs all 105 refinery benchmark scenarios through the three-tier intent router
to measure the exact Tier 1 / Tier 2 / Tier 3 resolution rates and overall routing accuracy.
"""

import os
import sys
import json
import time

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.router.task_router import auto_detect_task_intent, get_router_tier_metrics, reset_router_tier_metrics

BENCHMARK_PATH = os.path.join(os.path.dirname(__file__), "..", "benchmarks", "refinery_scenarios_v1.jsonl")

def evaluate_all_105_scenarios():
    print("================================================================================")
    print("  EVALUATING THREE-TIER ROUTER ACROSS ALL 105 REFINERY BENCHMARK SCENARIOS     ")
    print("================================================================================\n")

    if not os.path.exists(BENCHMARK_PATH):
        print(f"[ERROR] Benchmark dataset missing at {BENCHMARK_PATH}")
        sys.exit(1)

    scenarios = []
    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                scenarios.append(json.loads(line.strip()))

    total_scenarios = len(scenarios)
    print(f"Total scenarios loaded: {total_scenarios}")

    reset_router_tier_metrics()

    correct_count = 0
    t0 = time.perf_counter()

    for idx, scen in enumerate(scenarios, 1):
        prompt = scen["prompt"]
        exp_intent = scen["expected_intent"]

        res = auto_detect_task_intent(prompt)
        act_intent = "disambiguation" if res.is_ambiguous else res.task_type

        # Intent equivalence for compliance queries
        is_correct = (act_intent == exp_intent) or (exp_intent == "rule_check" and act_intent in ["rule_check", "doc_gen"]) or (exp_intent == "doc_gen" and act_intent in ["doc_gen", "rule_check"])
        if is_correct:
            correct_count += 1

    duration = time.perf_counter() - t0
    metrics = get_router_tier_metrics()

    print("\n--------------------------------------------------------------------------------")
    print("RESULTS: THREE-TIER USAGE BREAKDOWN (105 SCENARIOS)")
    print("--------------------------------------------------------------------------------")
    print(f"Total Benchmark Queries Evaluated  : {metrics['total_queries']}")
    print(f"Total Execution Time               : {duration:.2f} s (avg {duration*1000/metrics['total_queries']:.1f} ms/query)")
    print(f"Overall Routing Accuracy           : {correct_count}/{total_scenarios} ({correct_count/total_scenarios*100:.1f}%)")
    print("--------------------------------------------------------------------------------")
    print(f"Tier 1 (Fast Deterministic Router) : {metrics['tier_1_count']:>3}/{total_scenarios} ({metrics['tier_1_resolution_rate']:.1%})")
    print(f"Tier 2 (LLM Semantic Fallback)     : {metrics['tier_2_count']:>3}/{total_scenarios} ({metrics['tier_2_fallback_rate']:.1%})")
    print(f"Tier 3 (Disambiguation UI)         : {metrics['tier_3_count']:>3}/{total_scenarios} ({metrics['tier_3_disambiguation_rate']:.1%})")
    print("================================================================================\n")

if __name__ == "__main__":
    evaluate_all_105_scenarios()
