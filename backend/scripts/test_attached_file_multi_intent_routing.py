import sys
import os
import asyncio
import httpx
from uuid import UUID

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.router.task_router import auto_detect_task_intent
from app.router.lightweight_classifier import classify_intent_lightweight

BASE_URL = "http://127.0.0.1:8000"

async def test_stale_source_task_id_and_multi_intent():
    print("=====================================================================")
    print("   TEST: Attached File + Multi-Intent Routing + FK Safety Suite     ")
    print("=====================================================================")
    
    # 1. Test lightweight classifier multi-intent calibration
    prompt = "show how trend degradation+memo with real numbers"
    file_path = "c:/sih117/prototype/02_PMP-204_Degrading_2026-09-05.pdf"
    
    pred_intent, conf, scores, is_ambig, vocab_cov = classify_intent_lightweight(prompt, file_path)
    print(f"1. Classifier with attached file: intent={pred_intent}, conf={conf:.2f}, vocab_cov={vocab_cov:.2f}")
    assert pred_intent == "doc_gen"
    assert conf >= 0.70
    
    # 2. Test auto-router routing result
    route_res = auto_detect_task_intent(prompt=prompt, file_path=file_path)
    print(f"2. Auto-Router output: task_type={route_res.task_type}, reason={route_res.routing_reason}")
    assert route_res.task_type == "doc_gen"
    assert "Attached Document" in route_res.routing_reason
    
    # 3. Test live POST /tasks/auto with stale foreign key source_task_id
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{BASE_URL}/tasks/auto",
            json={
                "prompt": prompt,
                "file_path": file_path,
                "source_task_id": "00000000-0000-0000-0000-000000000000"
            },
            headers={"X-User-Role": "supervisor"}
        )
        print(f"3. POST /tasks/auto response status: {resp.status_code}")
        assert resp.status_code in [200, 202], f"Expected 200 or 202, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert data.get("task_type") == "doc_gen"
        assert data.get("source_task_id") is None, "Stale source_task_id should have been sanitized to None"
        assert not data.get("is_disambiguation")
        
        created_id = data.get("id")
        print(f" [PASS] Task successfully created with ID: {created_id} (task_type: {data.get('task_type')})")
        
        # Verify steps
        task_resp = await client.get(f"{BASE_URL}/tasks/{created_id}", headers={"X-User-Role": "supervisor"})
        assert task_resp.status_code == 200
        task_data = task_resp.json()
        assert len(task_data.get("steps", [])) >= 1
        router_step = task_data["steps"][0]
        assert router_step["tool_called"] == "auto_router"
        print(f" [PASS] Initial Auto-Router step confirmed: {router_step['description']}")

    print("\n=====================================================================")
    print("   ALL ATTACHED FILE MULTI-INTENT & FK SAFETY CHECKS PASSED!         ")
    print("=====================================================================")

if __name__ == "__main__":
    asyncio.run(test_stale_source_task_id_and_multi_intent())
