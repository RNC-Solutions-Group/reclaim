"""Background worker support for the desktop shell."""

from __future__ import annotations

from threading import Event
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

Job = Callable[[Callable[[dict[str, Any]], None], Event], Any]


class Task(QThread):
    """Run one application-service call off the GUI thread."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, job: Job, parent=None) -> None:
        super().__init__(parent)
        self._job = job
        self._stop = Event()

    def run(self) -> None:  # pragma: no cover - exercised through Qt event loop
        try:
            result = self._job(self.progress.emit, self._stop)
        except Exception as exc:  # noqa: BLE001 - GUI boundary
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()
