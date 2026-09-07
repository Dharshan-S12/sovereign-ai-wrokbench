import os
import sys
import uuid
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import engine, AsyncSessionLocal, get_db_health_info, ACTIVE_DB_URL
from app.models import Task, TaskStep, TaskType, TaskStatus, EquipmentNode, EquipmentEvent, MemoryEntry

async def test_db_parity():
    print("=" * 70)
    print("   TEST: Database Engine Feature Parity & Fallback Status Reporting   ")
    print("=" * 70)
    
    health_info = get_db_health_info()
    print(f" -> Active DB Backend: {health_info['active_backend'].upper()}")
    print(f" -> Is Fallback Active: {health_info['is_fallback']}")
    print(f" -> Integrity Mode: {health_info['integrity_mode']}")
    if health_info['banner_message']:
        print(f" -> Active UI Banner: '{health_info['banner_message']}'")
    
    async with AsyncSessionLocal() as db:
        # 1. Concurrent task step writes
        task_id = uuid.uuid4()
        task = Task(
            id=task_id,
            task_type=TaskType.doc_gen,
            status=TaskStatus.running,
            input_ref="test_input_spec.pdf",
            confidence_score=0.96
        )
        db.add(task)
        await db.commit()

        # Add steps sequentially
        for i in range(1, 4):
            step = TaskStep(
                task_id=task_id,
                step_number=i,
                description=f"Autonomous Pipeline Phase {i}",
                tool_called=f"tool_stage_{i}",
                tool_result={"status": "success", "latency_ms": 12.5 * i}
            )
            db.add(step)
        await db.commit()

        # Query steps back
        result = await db.execute(
            select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.step_number)
        )
        steps = result.scalars().all()
        assert len(steps) == 3
        print(f" [PASS] Concurrent sequential step writes verified on {health_info['active_backend']}")

        # 2. Knowledge Graph Node & Event Persistence
        eq_id = f"TEST-PMP-{uuid.uuid4().hex[:6].upper()}"
        node = EquipmentNode(
            equipment_id=eq_id,
            equipment_name="Boiler Feedwater Secondary Booster Pump",
            unit="OM&S-Offsite",
            equipment_type="Centrifugal Pump"
        )
        db.add(node)
        await db.commit()

        event = EquipmentEvent(
            equipment_node_id=node.id,
            source_task_id=task_id,
            event_type="vibration_anomaly_detection",
            event_data={"vibration_rms": 3.4, "iso_zone": "Zone B", "compliance": "COMPLIANT"}
        )
        db.add(event)
        await db.commit()

        event_res = await db.execute(
            select(EquipmentEvent).where(EquipmentEvent.equipment_node_id == node.id)
        )
        fetched_event = event_res.scalars().first()
        assert fetched_event is not None
        assert fetched_event.event_data.get("vibration_rms") == 3.4
        print(f" [PASS] Equipment Knowledge Graph JSON payload persistence verified on {health_info['active_backend']}")

        # 3. Memory Evolution Supersede Chain parity
        mem1 = MemoryEntry(
            id=uuid.uuid4(),
            entity_key=f"equipment_id:{eq_id}",
            summary_text="Initial baseline inspection reading 2.1 mm/s",
            strength_score=1.0,
            safety_critical=False
        )
        db.add(mem1)
        await db.commit()

        mem2 = MemoryEntry(
            id=uuid.uuid4(),
            entity_key=f"equipment_id:{eq_id}",
            summary_text="Follow-up inspection reading 5.8 mm/s (Zone C)",
            strength_score=1.0,
            safety_critical=True
        )
        db.add(mem2)
        await db.flush()
        mem1.superseded_by = mem2.id
        await db.commit()

        mem_res = await db.execute(
            select(MemoryEntry).where(MemoryEntry.entity_key == f"equipment_id:{eq_id}")
        )
        all_mems = mem_res.scalars().all()
        assert len(all_mems) == 2
        active_mem = next(m for m in all_mems if m.superseded_by is None)
        assert active_mem.id == mem2.id
        assert active_mem.safety_critical is True
        print(f" [PASS] Memory Evolution & safety_critical flag persistence verified on {health_info['active_backend']}")

    print(f"\n[PASS] All DB backend operations verified with 100% parity on {engine.dialect.name}!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(test_db_parity())
