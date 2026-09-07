"""
MRPL Sovereign Workbench — Step 4: LLM Fallback Routing Test Suite
Verifies that genuinely novel phrasing with low Tier 1 vocabulary coverage
(such as "give me the trend result" and its close variants) is caught by
Tier 2 (LLM semantic fallback) and routed to predictive_trend without triggering
Tier 3 disambiguation.
"""

import os
import sys

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.router.task_router import auto_detect_task_intent, get_router_tier_metrics, reset_router_tier_metrics
from app.router.lightweight_classifier import classify_intent_lightweight

def test_llm_fallback_trend_cases():
    print("================================================================================")
    print("  TEST: LLM FALLBACK ROUTING FOR UNRECOGNIZED / NOVEL TREND VOCABULARY          ")
    print("================================================================================\n")

    reset_router_tier_metrics()

    test_phrases = [
        "give me the trend result",
        "show me the trend",
        "what's the trend forecast",
        "trend analysis please"
    ]

    for phrase in test_phrases:
        print(f"Testing phrase: \"{phrase}\"")
        
        # 1. Inspect Tier 1 alone
        t1_intent, t1_conf, t1_dist, t1_ambig, t1_cov = classify_intent_lightweight(phrase)
        print(f"  [Tier 1 Classifier] intent={t1_intent}, conf={t1_conf:.3f}, coverage={t1_cov:.2f}, is_ambiguous={t1_ambig}")

        # 2. Run through 3-tier router
        res = auto_detect_task_intent(phrase)
        print(f"  [3-Tier Result] task_type='{res.task_type}', routed_by='{res.routed_by}', conf={res.confidence:.2f}, is_ambiguous={res.is_ambiguous}")
        print(f"  [Reasoning] {res.routing_reason}\n")

        # Assertions
        assert not res.is_ambiguous, f"Expected non-ambiguous routing for '{phrase}', got is_ambiguous=True"
        assert res.task_type == "predictive_trend", f"Expected task_type 'predictive_trend' for '{phrase}', got '{res.task_type}'"
        assert res.routed_by in ["fast_classifier", "llm_semantic_fallback"], f"Unexpected routed_by '{res.routed_by}'"

    metrics = get_router_tier_metrics()
    print("--------------------------------------------------------------------------------")
    print(f"Tier Metrics Summary: {metrics}")
    print("--------------------------------------------------------------------------------")
    print("[SUCCESS] All novel trend phrases successfully routed to predictive_trend without disambiguation!")

if __name__ == "__main__":
    test_llm_fallback_trend_cases()
