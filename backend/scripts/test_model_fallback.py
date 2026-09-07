"""
MRPL Sovereign Workbench — Model Fallback & Resilient Sourcing Verification
Simulates primary model dropouts / supply chain tampering and confirms automatic fallback
to next ranked model with transparent audit logging.
"""

import os
import sys
import json
import asyncio

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND_DIR)

from app.models.model_registry import (
    resolve_model_for_role,
    set_simulated_model_status,
    get_fallback_audit_logs,
    ModelRole,
    FALLBACK_LOG_PATH
)

async def test_model_fallback_flow():
    print("================================================================================")
    print("  ITEM 3: MODEL-SUBSTITUTION FALLBACK & RESILIENT SOURCING VERIFICATION")
    print("================================================================================\n")

    # Reset simulation state
    set_simulated_model_status(unavailable=[], untrusted=[])

    # 1. Normal Resolution: Drafting Role
    normal_res = await resolve_model_for_role(ModelRole.DRAFTING.value)
    print(f"1. Normal Role Resolution (Role='drafting'):")
    print(f"   * Selected Model : {normal_res.model_name}")
    print(f"   * Is Fallback    : {normal_res.is_fallback}")
    print(f"   * Primary Model  : {normal_res.primary_model}")
    print(f"   * Reason         : {normal_res.reason}\n")
    assert normal_res.model_name in ["qwen2.5:7b-instruct", "qwen2.5:3b"], "Should resolve to valid drafting candidate"

    # 2. Simulate Primary Model Dropout (qwen2.5:7b-instruct unavailable)
    print("2. Simulating Primary Model Dropout (qwen2.5:7b-instruct unavailable)...")
    set_simulated_model_status(unavailable=["qwen2.5:7b-instruct"], untrusted=[])

    fallback_res = await resolve_model_for_role(ModelRole.DRAFTING.value)
    print(f"   * Selected Model : {fallback_res.model_name}")
    print(f"   * Is Fallback    : {fallback_res.is_fallback}")
    print(f"   * Primary Model  : {fallback_res.primary_model}")
    print(f"   * Fallback Reason: {fallback_res.reason}\n")

    assert fallback_res.is_fallback is True, "Resolution must be flagged as fallback"
    assert fallback_res.model_name == "qwen2.5:3b", "Should fall back to next ranked model qwen2.5:3b"

    # 3. Simulate Primary Model Supply-Chain Tampering (qwen2.5:7b-instruct untrusted)
    print("3. Simulating Supply-Chain Tampering Check (qwen2.5:7b-instruct untrusted)...")
    set_simulated_model_status(unavailable=[], untrusted=["qwen2.5:7b-instruct"])

    tamper_fallback_res = await resolve_model_for_role(ModelRole.DRAFTING.value)
    print(f"   * Selected Model : {tamper_fallback_res.model_name}")
    print(f"   * Is Fallback    : {tamper_fallback_res.is_fallback}")
    print(f"   * Primary Model  : {tamper_fallback_res.primary_model}")
    print(f"   * Fallback Reason: {tamper_fallback_res.reason}\n")

    assert tamper_fallback_res.is_fallback is True, "Tampered model substitution must be flagged as fallback"
    assert tamper_fallback_res.model_name == "qwen2.5:3b", "Should bypass untrusted model and pick safe candidate"

    # 4. Verify Audit Log Persistence
    print("4. Verifying Fallback Audit Log Trail in storage/model_fallback_events.jsonl...")
    logs = get_fallback_audit_logs(limit=10)
    print(f"   * Found {len(logs)} fallback audit records.")
    assert len(logs) >= 2, "Must have recorded at least 2 fallback events"
    latest = logs[-1]
    print(f"   * Latest Record: Role='{latest['role']}', Primary='{latest['primary_model']}', Substituted='{latest['substituted_model']}'")
    print(f"   * Reason: {latest['fallback_reason']}\n")

    # Clean up simulation
    set_simulated_model_status(unavailable=[], untrusted=[])

    print("[SUCCESS] ModelRegistry fallback cascade and supply-chain resilience fully verified!")

if __name__ == "__main__":
    asyncio.run(test_model_fallback_flow())
