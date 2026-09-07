"""
MRPL Sovereign Workbench — Physics-Informed Trend Model Verification
Verifies exponential wear degradation V(t) = V0 * exp(k*t) vs linear & polynomial models.
"""

import os
import sys
import json
import numpy as np

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND_DIR)

from app.graph.trends import fit_models

def test_physics_trend_model():
    print("================================================================================")
    print("  ITEM 2: PHYSICS-INFORMED DEGRADATION MODEL FOR PREDICTIVE TRENDS")
    print("================================================================================\n")

    # Construct synthetic accelerating vibration degradation dataset
    # e.g. Day 0: 2.1 mm/s (Zone A), Day 30: 2.6 mm/s (Zone A), Day 60: 3.5 mm/s (Zone B), Day 90: 5.4 mm/s (Zone C)
    days = np.array([0.0, 30.0, 60.0, 90.0])
    vibrations = np.array([2.1, 2.6, 3.5, 5.4])

    print("Synthesized Accelerating Bearing Degradation Series:")
    for d, v in zip(days, vibrations):
        print(f"  * Day {d:4.1f} : {v:.2f} mm/s")
    print()

    fit_res = fit_models(days, vibrations)
    lin = fit_res["linear"]
    poly2 = fit_res["polynomial_deg2"]
    exp_wear = fit_res["exponential_wear"]

    print("Model Fit Comparison:")
    print(f"  1. Linear Model       : y = {lin['slope']:.4f}t + {lin['intercept']:.2f} (R² = {lin['r_squared']:.3f})")
    print(f"  2. Polynomial (deg=2) : y = {poly2['a']:.5f}t² + {poly2['b']:.4f}t + {poly2['c']:.2f} (R² = {poly2['r_squared']:.3f})")
    print(f"  3. Physics Exp Wear   : V(t) = {exp_wear['v0']:.3f} * exp({exp_wear['wear_constant_k']:.5f}t) (R² = {exp_wear['r_squared']:.3f})")
    print(f"                          Formula: {exp_wear['physics_formula']}\n")

    # Evaluate days to ISO Zone D critical threshold (7.1 mm/s)
    threshold = 7.1
    latest_val = vibrations[-1] # 5.4
    latest_day = days[-1]       # 90.0

    # 1. Linear days to 7.1
    lin_days = (threshold - latest_val) / lin["slope"]
    
    # 2. Exp days to 7.1
    t_breach_exp = np.log(threshold / exp_wear["v0"]) / exp_wear["wear_constant_k"]
    exp_days = t_breach_exp - latest_day

    print(f"Forecast Days to Critical Threshold ({threshold} mm/s) from Day {latest_day}:")
    print(f"  * Linear Extrapolation : {lin_days:.1f} days remaining")
    print(f"  * Physics Exp Model    : {exp_days:.1f} days remaining ({lin_days - exp_days:.1f} days earlier warning)")
    print()

    # Assertions
    assert exp_wear["r_squared"] >= 0.95, f"Exp wear R² {exp_wear['r_squared']} should be >= 0.95"
    assert exp_wear["is_accelerating"] is True, "Exp wear model should detect accelerating degradation"
    assert exp_days < lin_days, f"Physics model ({exp_days:.1f}d) must provide earlier warning than linear model ({lin_days:.1f}d)"

    print("[SUCCESS] Physics-informed exponential wear model verified as superior and conservative!")

if __name__ == "__main__":
    test_physics_trend_model()
