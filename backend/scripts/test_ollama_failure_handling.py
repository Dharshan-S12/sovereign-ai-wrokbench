import sys
import os
import asyncio
import time
from unittest.mock import patch
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.ollama_client import (
    generate_text,
    generate_vision,
    force_load_model,
    OllamaConnectionError,
    OllamaTimeoutError,
    OLLAMA_BASE_URL
)
from app.router.model_router import generate_with_escalation

async def test_case_a_unreachable():
    print("\n--- [Case A] Testing Ollama Unreachable Handling ---")
    with patch("app.models.ollama_client.OLLAMA_BASE_URL", "http://127.0.0.1:9999"):
        try:
            await generate_text("Ping test", model="qwen2.5:3b", timeout_seconds=2.0)
            assert False, "Should have raised OllamaConnectionError"
        except OllamaConnectionError as e:
            print(f" [PASS] Caught OllamaConnectionError cleanly: '{e}'")
            assert "check ollama is running" in str(e).lower()

async def test_case_b_mid_load_transient_retry():
    print("\n--- [Case B] Testing Transient Mid-Load Retry & Recovery ---")
    # Simulate first attempt throwing ConnectTimeout (transient model load delay) and second attempt succeeding
    call_count = 0
    original_post = httpx.AsyncClient.post

    async def mock_post(self, url, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1 and "/api/generate" in str(url) and kwargs.get("json", {}).get("prompt") != "":
            # First attempt: simulate transient timeout / model load delay
            raise httpx.ConnectTimeout("Model loading timeout on first handshake")
        return await original_post(self, url, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "post", mock_post):
        res = await generate_text("State the name of MRPL refinery in 3 words", model="qwen2.5:3b", timeout_seconds=60.0, max_retries=1)
        print(f" [PASS] Generation recovered on retry (attempts: {call_count}): {res[:80]}...")
        assert len(res) > 0
        assert call_count >= 2

async def test_case_c_hanging_timeout():
    print("\n--- [Case C] Testing Explicit Timeout Guardrail ---")
    try:
        # Extremely small timeout to trigger timeout guardrail deterministically
        await generate_text("Explain full refinery thermodynamics in 500 words", model="qwen2.5:3b", timeout_seconds=0.0001, max_retries=0)
        assert False, "Should have raised OllamaTimeoutError"
    except (OllamaTimeoutError, OllamaConnectionError) as e:
        print(f" [PASS] Caught timeout guardrail cleanly: '{e}'")
        assert "timed out" in str(e).lower() or "unavailable" in str(e).lower()

async def test_case_d_escalation_resilience():
    print("\n--- [Case D] Testing generate_with_escalation Model Resilience ---")
    # If primary model fails, escalation should gracefully fall back
    res, info = await generate_with_escalation(
        prompt="Explain vibration zone A in 10 words",
        system="Be concise.",
        min_length=10,
        fast_model="qwen2.5:3b",
        primary_model="non_existent_model_777",
        timeout_fast=30.0,
        timeout_primary=2.0
    )
    print(f" [PASS] generate_with_escalation handled missing/failing primary model: '{res[:80]}...' (escalation info: {info})")
    assert len(res) > 0

async def main():
    print("=====================================================================")
    print("   TEST: Ollama Model Loading, Connection & Failure Handling Suite   ")
    print("=====================================================================")
    start_time = time.time()
    
    await test_case_a_unreachable()
    await test_case_b_mid_load_transient_retry()
    await test_case_c_hanging_timeout()
    await test_case_d_escalation_resilience()
    
    elapsed = time.time() - start_time
    print(f"\n=====================================================================")
    print(f"   ALL OLLAMA FAILURE HANDLING TESTS PASSED in {elapsed:.2f}s!         ")
    print("=====================================================================")

if __name__ == "__main__":
    asyncio.run(main())
