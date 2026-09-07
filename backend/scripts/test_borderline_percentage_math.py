#!/usr/bin/env python3
"""
Regression Test Suite: Borderline Percentage Mathematics & Ensemble Gating Verification
Validates:
1. Exact mathematical percentage delta calculation ((actual - limit) / limit * 100).
2. Boundary behavior:
   - 0.0% at limit: Compliant, not a borderline violation. Ensemble bypassed.
   - 5.0% over limit: Non-compliant, borderline violation (0 < delta <= 10%). Ensemble triggered.
   - 10.0% over limit: Boundary case (inclusive). Non-compliant, borderline violation. Ensemble triggered.
   - 20.0% over limit (PMP-204 case): Non-compliant, unambiguous violation. Ensemble bypassed for direct deterministic verdict.
3. Multi-parameter isolation: A compliant reading within 10% of maximum (e.g. 79.5°C vs 80.0°C)
   does NOT mark an unambiguous violation (+20% vibration) as borderline.
"""

import sys
import os

# Ensure backend root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.rules.rule_engine import evaluate_rules


def test_borderline_percentage_math():
    print("=" * 70)
    print("RUNNING REGRESSION TEST: Borderline Percentage Math & Ensemble Gating")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # Case 1: Reading exactly at limit (0% over — compliant, not borderline violation)
    # -------------------------------------------------------------------------
    case1_data = {
        "equipment_id": "PMP-101",
        "vibration_velocity_rms": 4.5,
        "bearing_temperature": 60.0
    }
    r1 = evaluate_rules(case1_data)
    assert r1["evaluated"] is True, "Case 1 must be evaluated"
    vib1 = next(r for r in r1["rule_results"] if r["field"] == "vibration_velocity_rms")
    
    print("\n[CASE 1] Reading exactly at limit (4.5 mm/s vs 4.5 mm/s limit):")
    print(f"  Delta: {vib1['delta_pct']}% ({vib1['delta_label']})")
    print(f"  Passed: {vib1['passed']}, Borderline Violation: {vib1['is_borderline_violation']}")
    print(f"  Doc is_borderline (Ensemble Trigger): {r1['is_borderline']}")

    assert vib1["delta_pct"] == 0.0, f"Expected 0.0% delta, got {vib1['delta_pct']}"
    assert vib1["passed"] is True, "4.5 mm/s is within Zone B limit (<= 4.5), should pass"
    assert vib1["is_borderline_violation"] is False, "Compliant reading cannot be a borderline violation"
    assert r1["is_borderline"] is False, "Compliant reading at limit must NOT trigger ensemble borderline mode"

    # -------------------------------------------------------------------------
    # Case 2: Reading 5% over limit (4.725 mm/s vs 4.5 mm/s — genuinely borderline)
    # -------------------------------------------------------------------------
    case2_data = {
        "equipment_id": "PMP-102",
        "vibration_velocity_rms": 4.725,
        "bearing_temperature": 60.0
    }
    r2 = evaluate_rules(case2_data)
    assert r2["evaluated"] is True, "Case 2 must be evaluated"
    vib2 = next(r for r in r2["rule_results"] if r["field"] == "vibration_velocity_rms")

    print("\n[CASE 2] Reading 5% over limit (4.725 mm/s vs 4.5 mm/s limit):")
    print(f"  Delta: {vib2['delta_pct']}% ({vib2['delta_label']})")
    print(f"  Passed: {vib2['passed']}, Borderline Violation: {vib2['is_borderline_violation']}")
    print(f"  Doc is_borderline (Ensemble Trigger): {r2['is_borderline']}")

    assert vib2["delta_pct"] == 5.0, f"Expected +5.0% delta, got {vib2['delta_pct']}"
    assert vib2["passed"] is False, "4.725 mm/s exceeds Zone B limit (4.5), must fail"
    assert vib2["is_borderline_violation"] is True, "5.0% over is genuinely within 10% borderline band"
    assert r2["is_borderline"] is True, "Genuinely borderline violation MUST trigger ensemble mode"

    # -------------------------------------------------------------------------
    # Case 3: Reading exactly 10% over limit (4.95 mm/s vs 4.5 mm/s — boundary case)
    # Policy Decision: 0.0% < delta <= 10.0% is classified as borderline (inclusive)
    # -------------------------------------------------------------------------
    case3_data = {
        "equipment_id": "PMP-103",
        "vibration_velocity_rms": 4.95,
        "bearing_temperature": 60.0
    }
    r3 = evaluate_rules(case3_data)
    assert r3["evaluated"] is True, "Case 3 must be evaluated"
    vib3 = next(r for r in r3["rule_results"] if r["field"] == "vibration_velocity_rms")

    print("\n[CASE 3] Reading exactly 10% over limit (4.95 mm/s vs 4.5 mm/s limit — Boundary):")
    print(f"  Delta: {vib3['delta_pct']}% ({vib3['delta_label']})")
    print(f"  Passed: {vib3['passed']}, Borderline Violation: {vib3['is_borderline_violation']}")
    print(f"  Doc is_borderline (Ensemble Trigger): {r3['is_borderline']}")

    assert vib3["delta_pct"] == 10.0, f"Expected +10.0% delta, got {vib3['delta_pct']}"
    assert vib3["passed"] is False, "4.95 mm/s exceeds 4.5, must fail"
    assert vib3["is_borderline_violation"] is True, "Boundary case (10.0% exactly) is treated as borderline (inclusive)"
    assert r3["is_borderline"] is True, "Boundary case at 10.0% MUST trigger ensemble mode"

    # -------------------------------------------------------------------------
    # Case 4: Reading 20% over limit (5.4 mm/s vs 4.5 mm/s — clear violation)
    # -------------------------------------------------------------------------
    case4_data = {
        "equipment_id": "PMP-104",
        "vibration_velocity_rms": 5.4,
        "bearing_temperature": 60.0
    }
    r4 = evaluate_rules(case4_data)
    assert r4["evaluated"] is True, "Case 4 must be evaluated"
    vib4 = next(r for r in r4["rule_results"] if r["field"] == "vibration_velocity_rms")

    print("\n[CASE 4] Reading 20% over limit (5.4 mm/s vs 4.5 mm/s limit — Clear Violation):")
    print(f"  Delta: {vib4['delta_pct']}% ({vib4['delta_label']})")
    print(f"  Passed: {vib4['passed']}, Borderline Violation: {vib4['is_borderline_violation']}")
    print(f"  Doc is_borderline (Ensemble Trigger): {r4['is_borderline']}")

    assert vib4["delta_pct"] == 20.0, f"Expected +20.0% delta, got {vib4['delta_pct']}"
    assert vib4["passed"] is False, "5.4 mm/s exceeds 4.5, must fail"
    assert vib4["is_borderline_violation"] is False, "20.0% over is NOT borderline — it is a clear violation"
    assert r4["is_borderline"] is False, "Clear violation must BYPASS ensemble mode for direct deterministic path"

    # -------------------------------------------------------------------------
    # Case 5: Real PMP-204 Scenario (Vibration 5.4 mm/s, Bearing Temp 79.5 °C vs 80.0 °C)
    # Validates that temp near 80°C does NOT cause the document to be treated as borderline
    # when vibration is an unambiguous +20% Zone C violation.
    # -------------------------------------------------------------------------
    case5_data = {
        "equipment_id": "PMP-204",
        "vibration_velocity_rms": 5.4,
        "bearing_temperature": 79.5
    }
    r5 = evaluate_rules(case5_data)
    assert r5["evaluated"] is True, "Case 5 must be evaluated"
    vib5 = next(r for r in r5["rule_results"] if r["field"] == "vibration_velocity_rms")
    temp5 = next(r for r in r5["rule_results"] if r["field"] == "bearing_temperature")

    print("\n[CASE 5] Real PMP-204 Scenario (Vibration: 5.4 mm/s, Temp: 79.5 °C):")
    print(f"  Vibration Delta: {vib5['delta_pct']}% ({vib5['delta_label']}) | Passed: {vib5['passed']} | Borderline: {vib5['is_borderline_violation']}")
    print(f"  Temp Delta: {temp5['delta_pct']}% ({temp5['delta_label']}) | Passed: {temp5['passed']} | Borderline: {temp5['is_borderline_violation']}")
    print(f"  Doc Overall Verdict: {r5['overall_verdict']}")
    print(f"  Doc has_unambiguous_violation: {r5['has_unambiguous_violation']}")
    print(f"  Doc has_borderline_violation: {r5['has_borderline_violation']}")
    print(f"  Doc is_borderline (Ensemble Trigger): {r5['is_borderline']}")

    assert vib5["delta_pct"] == 20.0, "Vibration must be exactly +20.0%"
    assert vib5["is_borderline_violation"] is False, "Vibration +20% is NOT a borderline violation"
    assert temp5["delta_pct"] == -0.6, "Temperature must be -0.6% below limit"
    assert temp5["passed"] is True, "Temperature 79.5 <= 80.0 must be PASS"
    assert temp5["is_borderline_violation"] is False, "Compliant temperature is NOT a borderline violation"
    
    assert r5["overall_verdict"] == "NON_COMPLIANT", "Overall verdict must be NON_COMPLIANT"
    assert r5["has_unambiguous_violation"] is True, "Vibration +20% creates an unambiguous violation"
    assert r5["has_borderline_violation"] is False, "No parameter is in borderline violation band"
    assert r5["is_borderline"] is False, "Document must NOT be marked borderline (Ensemble must be BYPASSED!)"

    print("\n" + "=" * 70)
    print("ALL REGRESSION TESTS PASSED: Borderline math & ensemble gating verified!")
    print("=" * 70)


if __name__ == "__main__":
    test_borderline_percentage_math()
