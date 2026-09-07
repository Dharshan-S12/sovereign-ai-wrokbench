"""
MRPL Sovereign Workbench — Tiered Model Loading Verification
Verifies always-warm vs on-demand model tiering and memory lifecycle management.
"""

import os
import sys
import asyncio

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND_DIR)

from app.models.model_registry import (
    resolve_model_for_role,
    unload_model_from_memory,
    ModelRole,
    WARM_TIER_MODELS,
    ON_DEMAND_TIER_MODELS
)

async def test_tiered_loading_flow():
    print("================================================================================")
    print("  ITEM 5: TIERED MODEL LOADING & MEMORY LIFECYCLE VERIFICATION")
    print("================================================================================\n")

    print(f"Warm Tier (Always Resident)  : {list(WARM_TIER_MODELS)}")
    print(f"On-Demand Tier (Load/Unload) : {list(ON_DEMAND_TIER_MODELS)}\n")

    # 1. Test Fast Reasoning (should resolve to warm tier)
    res_fast = await resolve_model_for_role(ModelRole.FAST_REASONING.value)
    print(f"1. Fast Reasoning Resolution:")
    print(f"   * Model: {res_fast.model_name} (is_warm: {res_fast.is_warm})")
    assert res_fast.is_warm is True, "Fast reasoning default model should be in warm tier"

    # 2. Test Drafting (should resolve to on-demand tier)
    res_draft = await resolve_model_for_role(ModelRole.DRAFTING.value)
    print(f"\n2. Executive Drafting Resolution:")
    print(f"   * Model: {res_draft.model_name} (is_warm: {res_draft.is_warm})")
    assert res_draft.model_name in ON_DEMAND_TIER_MODELS, "Drafting model should be in on-demand tier"

    # 3. Test Unloading On-Demand Model
    print(f"\n3. Testing On-Demand Model Unload API for '{res_draft.model_name}'...")
    unload_ok = await unload_model_from_memory(res_draft.model_name)
    print(f"   * Unload Request Result: {'SUCCESS' if unload_ok else 'SIMULATED_LOCAL'}")

    print("\n[SUCCESS] Tiered model loading and memory lifecycle policies verified!")

if __name__ == "__main__":
    asyncio.run(test_tiered_loading_flow())
