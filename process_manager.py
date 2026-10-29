"""Tracks simulated command work without launching operating-system processes."""
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4


@dataclass(frozen=True)
class VirtualTask:
    id: str
    session_id: str
    description: str
    started_at: str


class ProcessManager:
    def __init__(self):
        self._tasks = {}
        self._lock = RLock()

    def start(self, session_id, description):
        task = VirtualTask(
            id=uuid4().hex,
            session_id=session_id,
            description=description,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._tasks[task.id] = task
        return task.id

    def finish(self, task_id):
        with self._lock:
            return self._tasks.pop(task_id, None)

    def active_for(self, session_id):
        with self._lock:
            return [task for task in self._tasks.values() if task.session_id == session_id]

    def active_tasks(self):
        with self._lock:
            return list(self._tasks.values())
