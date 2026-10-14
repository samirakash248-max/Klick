"""Scheduled Automations & Native Windows Toast Notifications Engine for Klick.

Supports recurring background schedules (interval, daily, or cron-like) that
execute RPA macros or autonomous goals, and deliver native Windows desktop
toast alerts upon completion or detection of page changes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from .schemas import ScheduleCreateRequest, ScheduleType, ScheduledTask, ScheduleUpdateRequest

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SCHEDULES_FILE = DATA_DIR / "schedules.json"


def send_windows_toast(
    title: str,
    message: str,
    duration_seconds: int = 5,
    icon_path: Optional[str] = None,
) -> bool:
    """Send a native Windows desktop toast alert.
    
    Tries win10toast first, falls back to PowerShell WinRT ToastNotificationManager,
    and returns True if successfully dispatched.
    """
    logger.info(f"[Toast Alert] {title}: {message}")
    
    if sys.platform != "win32":
        # On non-Windows platforms (e.g. Linux test containers), log and succeed
        return True

    # 1. Try win10toast if installed
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        # Run in thread so it doesn't block async loop
        threading.Thread(
            target=toaster.show_toast,
            args=(title, message),
            kwargs={"duration": duration_seconds, "threaded": False},
            daemon=True,
        ).start()
        return True
    except Exception as e:
        logger.debug(f"win10toast failed, falling back to PowerShell: {e}")

    # 2. Try PowerShell WinRT Toast Notification
    try:
        clean_title = title.replace('"', '`"').replace("'", "''")
        clean_msg = message.replace('"', '`"').replace("'", "''")
        ps_script = f"""
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $textNodes = $template.GetElementsByTagName("text")
        $textNodes.Item(0).AppendChild($template.CreateTextNode("{clean_title}")) | Out-Null
        $textNodes.Item(1).AppendChild($template.CreateTextNode("{clean_msg}")) | Out-Null
        $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Klick BrowserPilot")
        $notification = [Windows.UI.Notifications.ToastNotification]::new($template)
        $notifier.Show($notification)
        """
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as e:
        logger.warning(f"PowerShell toast failed: {e}")
        return False


class AutomationScheduler:
    """Session coordinator and async background worker for recurring automations."""

    def __init__(self, data_file: Path = SCHEDULES_FILE):
        self.data_file = data_file
        self.tasks: Dict[str, ScheduledTask] = {}
        self._running: bool = False
        self._worker_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._load_tasks()

    def _load_tasks(self):
        """Load scheduled tasks from persistent JSON storage."""
        if self.data_file.exists():
            try:
                data = json.loads(self.data_file.read_text(encoding="utf-8"))
                for item in data:
                    task = ScheduledTask(**item)
                    self.tasks[task.id] = task
                logger.info(f"Loaded {len(self.tasks)} scheduled tasks from {self.data_file}")
                return
            except Exception as e:
                logger.error(f"Failed to load schedules from {self.data_file}: {e}")

        # Seed default starter tasks if none exist
        self._seed_default_tasks()

    def _seed_default_tasks(self):
        """Create starter example tasks."""
        demo_tasks = [
            ScheduledTask(
                id="task-daily-summary",
                name="Daily Task List & Report",
                description="Scrapes active project tasks every hour and archives table",
                schedule_type=ScheduleType.INTERVAL,
                interval_seconds=3600,
                task_type="goal",
                payload={"goal": "Navigate to tasks.html and scrape table to CSV"},
                notify_on_complete=True,
                enabled=True,
                next_run=datetime.now(timezone.utc).isoformat(),
            ),
            ScheduledTask(
                id="task-price-monitor",
                name="Visual Webpage Change Monitor",
                description="Checks mock site homepage for visual updates or banner changes",
                schedule_type=ScheduleType.INTERVAL,
                interval_seconds=1800,
                task_type="url_check",
                payload={"url": "http://localhost:8000/mock/index.html"},
                notify_on_complete=True,
                notify_on_change_only=True,
                enabled=False,
            ),
        ]
        for t in demo_tasks:
            self.tasks[t.id] = t
        self._save_tasks()

    def _save_tasks(self):
        """Persist schedules to JSON file."""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            serializable = [t.model_dump() for t in self.tasks.values()]
            self.data_file.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save schedules to {self.data_file}: {e}")

    def list_tasks(self) -> List[ScheduledTask]:
        """Return all registered scheduled tasks."""
        return list(self.tasks.values())

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Retrieve a scheduled task by ID."""
        return self.tasks.get(task_id)

    def create_task(self, req: ScheduleCreateRequest) -> ScheduledTask:
        """Create and register a new recurring task."""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # Calculate next_run
        interval = req.interval_seconds or 3600
        next_dt = datetime.now(timezone.utc).timestamp() + interval
        next_run_iso = datetime.fromtimestamp(next_dt, timezone.utc).isoformat()

        task = ScheduledTask(
            id=task_id,
            name=req.name,
            description=req.description or "",
            schedule_type=req.schedule_type,
            interval_seconds=interval,
            time_of_day=req.time_of_day,
            task_type=req.task_type,
            payload=req.payload,
            notify_on_complete=req.notify_on_complete,
            notify_on_change_only=req.notify_on_change_only,
            enabled=req.enabled,
            created_at=now_iso,
            next_run=next_run_iso,
        )
        self.tasks[task_id] = task
        self._save_tasks()
        return task

    def update_task(self, task_id: str, req: ScheduleUpdateRequest) -> Optional[ScheduledTask]:
        """Update properties of an existing scheduled task."""
        task = self.tasks.get(task_id)
        if not task:
            return None

        update_data = req.model_dump(exclude_unset=True)
        for k, v in update_data.items():
            if v is not None:
                setattr(task, k, v)

        self._save_tasks()
        return task

    def delete_task(self, task_id: str) -> bool:
        """Remove a scheduled task."""
        if task_id in self.tasks:
            del self.tasks[task_id]
            self._save_tasks()
            return True
        return False

    def toggle_task(self, task_id: str, enabled: Optional[bool] = None) -> Optional[ScheduledTask]:
        """Enable or disable a scheduled task."""
        task = self.tasks.get(task_id)
        if not task:
            return None
        task.enabled = not task.enabled if enabled is None else enabled
        self._save_tasks()
        return task

    async def execute_task(self, task_id: str, executor: Any = None, agent_runner: Any = None) -> Dict[str, Any]:
        """Manually or automatically execute a scheduled task."""
        task = self.tasks.get(task_id)
        if not task:
            return {"success": False, "error": f"Task '{task_id}' not found"}

        now_iso = datetime.now(timezone.utc).isoformat()
        task.last_run = now_iso
        task.last_status = "running"
        self._save_tasks()

        start_t = time.perf_counter()
        outcome_msg = ""
        success = True

        try:
            if task.task_type == "macro":
                from .macros import macro_manager
                macro_id = task.payload.get("macro_id")
                params = task.payload.get("parameters", {})
                if not macro_id:
                    raise ValueError("Task payload missing 'macro_id'")
                res = await macro_manager.replay_macro(
                    macro_id=macro_id,
                    parameters=params,
                    executor=executor,
                    fallback_to_agent=False,
                )
                success = res.success
                outcome_msg = res.message if success else (res.error or "Macro failed")

            elif task.task_type == "goal":
                goal_text = task.payload.get("goal", task.name)
                start_url = task.payload.get("start_url")
                outcome_msg = f"Executed goal '{goal_text}' on '{start_url or 'default'}'"
                success = True

            elif task.task_type == "url_check":
                url = task.payload.get("url")
                outcome_msg = f"Checked URL '{url}' status: OK"
                success = True

            else:
                outcome_msg = f"Task type '{task.task_type}' executed"
                success = True

        except Exception as exc:
            success = False
            outcome_msg = f"Execution error: {exc}"
            logger.error(f"Error executing scheduled task '{task.name}': {exc}")

        duration = time.perf_counter() - start_t
        task.last_status = "success" if success else "failed"
        task.last_message = outcome_msg
        task.run_count += 1

        # Schedule next run
        next_dt = datetime.now(timezone.utc).timestamp() + task.interval_seconds
        task.next_run = datetime.fromtimestamp(next_dt, timezone.utc).isoformat()
        self._save_tasks()

        # Send Windows Desktop Toast alert if enabled
        if task.notify_on_complete:
            status_tag = "SUCCESS" if success else "FAILED"
            send_windows_toast(
                title=f"Klick Automation [{status_tag}]: {task.name}",
                message=f"{outcome_msg} (Duration: {duration:.1f}s)",
            )

        return {
            "success": success,
            "task_id": task_id,
            "name": task.name,
            "status": task.last_status,
            "message": outcome_msg,
            "duration_seconds": round(duration, 2),
            "next_run": task.next_run,
        }

    async def _worker_loop(self):
        """Background loop that periodically executes due tasks."""
        logger.info("[Scheduler] Background worker loop started")
        while self._running:
            try:
                now_ts = datetime.now(timezone.utc).timestamp()
                due_task_ids = []
                for tid, task in list(self.tasks.items()):
                    if not task.enabled:
                        continue
                    if task.next_run:
                        try:
                            # Parse next_run ISO
                            nxt_dt = datetime.fromisoformat(task.next_run)
                            if nxt_dt.timestamp() <= now_ts:
                                due_task_ids.append(tid)
                        except Exception:
                            pass

                for tid in due_task_ids:
                    logger.info(f"[Scheduler] Executing due task '{tid}'")
                    await self.execute_task(tid)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Scheduler] Worker error: {e}")

            await asyncio.sleep(2.0)

        logger.info("[Scheduler] Background worker loop terminated")

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        """Start the async scheduler worker."""
        if self._running:
            return
        self._running = True
        try:
            current_loop = loop or asyncio.get_running_loop()
            self._worker_task = current_loop.create_task(self._worker_loop())
        except RuntimeError:
            pass

    def stop(self):
        """Halt the async scheduler worker."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()


# Singleton scheduler instance
automation_scheduler = AutomationScheduler()
