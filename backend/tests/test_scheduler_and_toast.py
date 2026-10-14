"""Unit tests for Scheduled Automations & Native Windows Toast Notifications Engine."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest

from backend.app.scheduler import (
    AutomationScheduler,
    send_windows_toast,
)
from backend.app.schemas import (
    ScheduleCreateRequest,
    ScheduleType,
    ScheduledTask,
    ScheduleUpdateRequest,
    ToastNotificationRequest,
)


def test_toast_notification_dispatch():
    """Verify toast notification dispatcher runs without exceptions."""
    dispatched = send_windows_toast(
        title="Test Klick Notification",
        message="Verification toast notification test",
        duration_seconds=2,
    )
    assert dispatched is True


def test_scheduler_crud_lifecycle(tmp_path: Path):
    """Verify scheduler creates, retrieves, updates, and deletes tasks."""
    test_file = tmp_path / "test_schedules.json"
    scheduler = AutomationScheduler(data_file=test_file)

    # 1. Create task
    req = ScheduleCreateRequest(
        name="Hourly Price Check",
        description="Monitor laptop prices",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=120,
        task_type="goal",
        payload={"goal": "Check price on amazon"},
        notify_on_complete=True,
    )
    task = scheduler.create_task(req)
    assert task.id.startswith("task-")
    assert task.name == "Hourly Price Check"
    assert task.interval_seconds == 120
    assert task.enabled is True

    # 2. Get task
    fetched = scheduler.get_task(task.id)
    assert fetched is not None
    assert fetched.name == task.name

    # 3. Update task
    upd = ScheduleUpdateRequest(
        name="Bi-hourly Price Check",
        interval_seconds=7200,
        enabled=False,
    )
    updated = scheduler.update_task(task.id, upd)
    assert updated is not None
    assert updated.name == "Bi-hourly Price Check"
    assert updated.interval_seconds == 7200
    assert updated.enabled is False

    # 4. Toggle task
    toggled = scheduler.toggle_task(task.id)
    assert toggled is not None
    assert toggled.enabled is True

    # 5. Delete task
    deleted = scheduler.delete_task(task.id)
    assert deleted is True
    assert scheduler.get_task(task.id) is None


@pytest.mark.asyncio
async def test_scheduler_execute_task(tmp_path: Path):
    """Verify task execution records status and updates telemetry."""
    test_file = tmp_path / "exec_schedules.json"
    scheduler = AutomationScheduler(data_file=test_file)

    task = scheduler.create_task(ScheduleCreateRequest(
        name="Sample Check",
        task_type="url_check",
        payload={"url": "http://localhost:8000/mock/index.html"},
        interval_seconds=60,
        notify_on_complete=False,
    ))

    result = await scheduler.execute_task(task.id)
    assert result["success"] is True
    assert result["status"] == "success"
    assert "Checked URL" in result["message"]

    # Check task object updated
    saved_task = scheduler.get_task(task.id)
    assert saved_task.last_status == "success"
    assert saved_task.run_count == 1
    assert saved_task.last_run is not None
    assert saved_task.next_run is not None
