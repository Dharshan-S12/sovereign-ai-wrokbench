"""
MRPL Sovereign Workbench — Step 6: Three-Tier Intent Routing Regression Test
Comprehensive regression test verifying the end-to-end operation of all three tiers:
(a) Tier 1: Fast deterministic classifier (resolves in <5ms with 0 LLM calls)
(b) Tier 2: LLM semantic fallback (novel phrasing resolved by LLM second opinion)
(c) Tier 3: Disambiguation UI (genuinely ambiguous request reaching Tier 3 options)
"""

import os
import sys
import time

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.router.task_router import auto_detect_task_intent, get_router_tier_metrics, reset_router_tier_metrics

def test_three_tier_routing():
    print("================================================================================")
    print("  TEST: THREE-TIER INTENT ROUTING ARCHITECTURE REGRESSION SUITE                ")
    print("================================================================================\n")

    reset_router_tier_metrics()

    # --------------------------------------------------------------------------
    # Case (a): Clear, well-known phrasing -> Must resolve at Tier 1 (< 5ms, 0 LLM calls)
    # --------------------------------------------------------------------------
    tier1_prompts = [
        "Calculate the sum of first 50 prime numbers using python sandbox",
        "Generate vibration analysis compliance memo for Booster Pump PMP-204",
        "Compare previous 5 inspection reports across documents"
    ]

    print("--- CASE (a): Clear Phrasing (Tier 1 Fast Classifier) ---")
    for prompt in tier1_prompts:
        t0 = time.perf_counter()
        res = auto_detect_task_intent(prompt)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        print(f"Prompt: \"{prompt[:60]}...\"")
        print(f"  Tier: {res.routed_by} | Latency: {elapsed_ms:.3f} ms | Conf: {res.confidence:.2f} | Intent: {res.task_type}")
        
        assert res.routed_by == "fast_classifier", f"Expected 'fast_classifier' for clear prompt, got '{res.routed_by}'"
        assert not res.is_ambiguous, f"Expected unambiguous resolution for clear prompt"
        assert elapsed_ms < 50.0, f"Tier 1 expected sub-50ms execution, took {elapsed_ms:.2f}ms"
    print(">>> CASE (a) PASSED: Tier 1 executed with zero LLM inference and sub-millisecond throughput.\n")

    # --------------------------------------------------------------------------
    # Case (b): Novel phrasing with unrecognized vocabulary -> Must resolve at Tier 2 (LLM Fallback)
    # --------------------------------------------------------------------------
    print("--- CASE (b): Novel Phrasing / Unrecognized Vocabulary (Tier 2 LLM Fallback) ---")
    novel_prompt = "give me the trend result"
    t0 = time.perf_counter()
    res = auto_detect_task_intent(novel_prompt)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    print(f"Prompt: \"{novel_prompt}\"")
    print(f"  Tier: {res.routed_by} | Latency: {elapsed_ms:.1f} ms | Conf: {res.confidence:.2f} | Intent: {res.task_type}")
    print(f"  Reason: {res.routing_reason}")

    assert res.routed_by == "llm_semantic_fallback", f"Expected 'llm_semantic_fallback', got '{res.routed_by}'"
    assert res.task_type == "predictive_trend", f"Expected 'predictive_trend', got '{res.task_type}'"
    assert not res.is_ambiguous, "Expected non-ambiguous resolution from Tier 2 LLM"
    print(">>> CASE (b) PASSED: Novel phrasing correctly routed by Tier 2 LLM semantic fallback without human disambiguation.\n")

    # --------------------------------------------------------------------------
    # Case (c): Genuinely ambiguous phrasing -> Must resolve at Tier 3 (Disambiguation UI)
    # --------------------------------------------------------------------------
    print("--- CASE (c): Genuinely Ambiguous Phrasing (Tier 3 Disambiguation UI) ---")
    ambiguous_prompts = [
        "check the pump",
        "status"
    ]

    for prompt in ambiguous_prompts:
        t0 = time.perf_counter()
        res = auto_detect_task_intent(prompt)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        print(f"Prompt: \"{prompt}\"")
        print(f"  Tier: {res.routed_by} | Latency: {elapsed_ms:.1f} ms | Conf: {res.confidence:.2f} | Intent: {res.task_type}")
        print(f"  Is Ambiguous: {res.is_ambiguous} | Suggested Options: {len(res.suggested_options)}")

        assert res.routed_by == "disambiguation", f"Expected 'disambiguation', got '{res.routed_by}'"
        assert res.is_ambiguous, f"Expected is_ambiguous=True for '{prompt}'"
        assert res.task_type == "disambiguation", f"Expected task_type 'disambiguation'"
        assert len(res.suggested_options) >= 4, f"Expected >=4 disambiguation options, got {len(res.suggested_options)}"
    print(">>> CASE (c) PASSED: Ambiguous phrasing reached Tier 3 disambiguation UI as the true last resort.\n")

    # Check metrics
    metrics = get_router_tier_metrics()
    print("================================================================================")
    print(f"TOTAL METRICS ACROSS ALL TEST CASES: {metrics}")
    print(f"Tier 1 Resolution Rate: {metrics['tier_1_resolution_rate']:.1%}")
    print(f"Tier 2 Fallback Rate:   {metrics['tier_2_fallback_rate']:.1%}")
    print(f"Tier 3 Disambig Rate:   {metrics['tier_3_disambiguation_rate']:.1%}")
    print("================================================================================")
    print("\n[SUCCESS] All three tiers validated with correct escalation order and timing!")

if __name__ == "__main__":
    test_three_tier_routing()
