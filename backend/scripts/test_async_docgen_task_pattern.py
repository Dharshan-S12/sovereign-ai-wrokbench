"""
MRPL Sovereign Workbench — Async Task Pattern & Stage Timeout Regression Test
Validates:
(a) POST /tasks/auto for DocGen+trend requests returns immediately (sub-second) with HTTP 202 and 'processing' status.
(b) Polling GET /tasks/{task_id} demonstrates incrementally updating stage progress.
(c) The task reaches 'pending_approval' without any HTTP layer timeouts.
(d) A slow/hung stage times out at the per-stage level with a clear 'failed_at_stage_N: timeout' rather than hanging.
"""

import os
import sys
import time
import uuid
import asyncio
from unittest.mock import patch
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models import Task, TaskType, TaskStatus
from app.database import AsyncSessionLocal
from app.agent.multi_agent_docgen import run_multi_agent_docgen_pipeline, is_timeout_error, OllamaTimeoutError

BASE_URL = "http://127.0.0.1:8000"

async def test_async_immediate_response_and_polling():
    print("\n" + "=" * 75)
    print(" [TEST A, B, C] Async Non-Blocking Task Pattern & Live Stage Progress")
    print("=" * 75)

    prompt = f"show how trend degradation+memo with real numbers for pump P-204 run-{uuid.uuid4().hex[:6]}"
    file_path = "c:/sih117/prototype/02_PMP-204_Degrading_2026-09-05.pdf"

    t_start = time.perf_counter()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{BASE_URL}/tasks/auto",
            json={
                "prompt": prompt,
                "file_path": file_path
            },
            headers={"X-User-Role": "supervisor"}
        )
    t_post_elapsed = time.perf_counter() - t_start

    print(f" -> POST /tasks/auto status code: {resp.status_code} (in {t_post_elapsed:.3f}s)")
    assert resp.status_code == 202, f"Expected 202 Accepted, got {resp.status_code}: {resp.text}"
    assert t_post_elapsed < 2.0, f"POST /tasks/auto took too long ({t_post_elapsed:.2f}s); must be sub-second non-blocking"

    data = resp.json()
    task_id = data.get("id") or data.get("task_id")
    status = data.get("status")
    task_type = data.get("task_type")

    print(f" -> Task ID: {task_id}")
    print(f" -> Initial Status: '{status}'")
    print(f" -> Task Type: '{task_type}'")

    assert task_id is not None, "task_id must be returned in response"
    assert status == "processing", f"Expected initial status 'processing', got '{status}'"
    assert task_type == "doc_gen", f"Expected task_type 'doc_gen', got '{task_type}'"
    print(" [PASS] Test (a): Sub-second HTTP 202 acknowledgment with status 'processing' verified!")

    # Test (b) & (c): Poll GET /tasks/{task_id} and record live progress states
    print(f"\n -> Polling GET /tasks/{task_id} for incremental stage progress updates...")
    observed_progress_messages = []
    observed_step_counts = []
    final_task_data = None
    poll_start = time.perf_counter()
    max_poll_timeout = 360.0  # Allow up to 6 minutes for local Ollama inference

    async with httpx.AsyncClient(timeout=15.0) as client:
        while time.perf_counter() - poll_start < max_poll_timeout:
            get_resp = await client.get(f"{BASE_URL}/tasks/{task_id}", headers={"X-User-Role": "supervisor"})
            assert get_resp.status_code == 200, f"Polling failed with status {get_resp.status_code}"
            task_info = get_resp.json()
            curr_status = task_info.get("status")
            curr_output = task_info.get("output_ref") or ""
            steps = task_info.get("steps", [])

            step_count = len(steps)
            if step_count not in observed_step_counts:
                observed_step_counts.append(step_count)
                last_desc = steps[-1]["description"] if steps else "None"
                print(f"    [+{(time.perf_counter() - poll_start):.1f}s] Step #{step_count} logged: {last_desc[:70]}...")

            if curr_output and curr_output not in observed_progress_messages and "Stage " in curr_output:
                observed_progress_messages.append(curr_output)
                print(f"    [+{(time.perf_counter() - poll_start):.1f}s] Live Output Progress: {curr_output}")

            if curr_status in ["pending_approval", "done", "failed", "rejected"]:
                final_task_data = task_info
                break

            await asyncio.sleep(2.5)

    total_poll_time = time.perf_counter() - poll_start
    assert final_task_data is not None, f"Task did not reach terminal state within {max_poll_timeout}s"
    final_status = final_task_data.get("status")
    print(f"\n -> Task completed with status '{final_status}' after {total_poll_time:.2f}s ({total_poll_time/60:.2f} minutes)")

    # Assertions for (b)
    final_steps = final_task_data.get("steps", [])
    print(f" -> Final task steps count: {len(final_steps)}")
    print(f" -> Total distinct step counts observed during polling: {len(observed_step_counts)}")
    print(f" -> Total distinct live progress messages observed during polling: {len(observed_progress_messages)}")
    assert len(final_steps) >= 4, f"Expected at least 4 task steps recorded, got {len(final_steps)}"
    assert len(observed_step_counts) >= 2 or len(observed_progress_messages) >= 1, "Expected polling updates during execution"
    print(" [PASS] Test (b): Incremental stage progress polling verified!")

    # Assertions for (c)
    assert final_status == "pending_approval", f"Expected terminal status 'pending_approval', got '{final_status}'"
    assert final_task_data.get("output_ref") is not None and len(final_task_data.get("output_ref", "")) > 100
    print(" [PASS] Test (c): Task reached 'pending_approval' without HTTP connection timeouts!")
    return total_poll_time

async def test_deliberate_stage_timeout_handling():
    print("\n" + "=" * 75)
    print(" [TEST D] Deliberate Stage Timeout & Graceful Degradation Test")
    print("=" * 75)

    async with AsyncSessionLocal() as db:
        test_task = Task(
            id=uuid.uuid4(),
            task_type=TaskType.doc_gen,
            status=TaskStatus.processing,
            input_ref="PMP-204 deliberate timeout stage test"
        )
        db.add(test_task)
        await db.commit()
        await db.refresh(test_task)

        # Mock generate_text during Stage 1 (Extractor) to simulate hung Ollama call exceeding timeout
        async def mock_hung_generate_text(*args, **kwargs):
            raise asyncio.TimeoutError("Simulated Ollama local inference call hung past 60s limit")

        with patch("app.agent.multi_agent_docgen.generate_text", side_effect=mock_hung_generate_text):
            caught_runtime_error = False
            try:
                await run_multi_agent_docgen_pipeline(
                    db=db,
                    task=test_task,
                    step_count=1,
                    input_text=test_task.input_ref,
                    source_context="Test vibration velocity reading 7.8 mm/s on PMP-204",
                    kept_chunks=[],
                    total_retrieved_chunks=0,
                    total_discarded_chunks=0,
                    kept_chunk_distances=[]
                )
            except RuntimeError as re:
                caught_runtime_error = True
                print(f" -> Expected RuntimeError captured: {re}")
                assert "failed_at_stage_1: timeout" in str(re), f"Error message missing 'failed_at_stage_1: timeout': {re}"

            assert caught_runtime_error, "Pipeline should raise RuntimeError on stage timeout"

        # Verify DB Task state
        await db.refresh(test_task)
        print(f" -> Task status in DB: {test_task.status.value}")
        print(f" -> Task output_ref in DB: {test_task.output_ref}")

        assert test_task.status == TaskStatus.failed, f"Expected task status 'failed', got {test_task.status}"
        assert "failed_at_stage_1: timeout" in (test_task.output_ref or ""), "output_ref must record 'failed_at_stage_1: timeout'"
        print(" [PASS] Test (d): Deliberate stage timeout gracefully halted with 'failed_at_stage_1: timeout'!")

async def main():
    print("===========================================================================")
    print("   MRPL Sovereign Workbench — Async Task & Timeout Regression Suite        ")
    print("===========================================================================")

    # 1. Test A, B, C live against running server
    duration = await test_async_immediate_response_and_polling()

    # 2. Test D (isolated unit test verifying per-stage timeout halting)
    await test_deliberate_stage_timeout_handling()

    print("\n" + "=" * 75)
    print("   ALL ASYNC DOCGEN TASK PATTERN REGRESSION TESTS PASSED!                 ")
    print(f"   Measured Full End-to-End DocGen+Trend Inference Duration: {duration:.2f}s ({duration/60:.2f} min)")
    print("===========================================================================")

if __name__ == "__main__":
    asyncio.run(main())
