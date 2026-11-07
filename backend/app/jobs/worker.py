"""서버 안에서 도는 처리 작업 스레드 하나. 처리 대기 세션을 하나씩 계산한다.

- 세션 상태는 DB에 있으므로 서버가 다시 시작해도 남은 세션을 이어서 처리한다.
- 한 번에 하나만 계산해 메모리를 넘지 않게 한다.
- complete를 받으면 wake()로 바로 깨운다. 그 밖에는 몇 초마다 확인한다.
"""

import logging
import threading
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.jobs.processing import claim_next, process_session
from app.storage.s3 import RecordingStorage

logger = logging.getLogger(__name__)

IDLE_S = 5.0


class ProcessingWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        storage_factory: Callable[[], RecordingStorage],
        idle_s: float = IDLE_S,
    ) -> None:
        self.session_factory = session_factory
        self.storage_factory = storage_factory
        self.idle_s = idle_s
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> bool:
        """세션 하나를 처리했으면 참"""
        with self.session_factory() as db:
            session_id = claim_next(db)
            if session_id is None:
                return False
            process_session(db, self.storage_factory(), session_id)
            return True

    def _run(self) -> None:
        logger.info("처리 작업 시작")
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                # DB 접속 끊김 등. 스레드는 멈추지 않고 잠시 뒤 다시 시도한다.
                logger.exception("처리 작업 오류")
                worked = False
            if not worked:
                self._wake.wait(self.idle_s)
                self._wake.clear()
        logger.info("처리 작업 멈춤")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="processing-worker", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)


def default_worker() -> ProcessingWorker:
    from app.db.engine import get_engine
    from app.storage.s3 import get_storage

    return ProcessingWorker(lambda: Session(get_engine(), expire_on_commit=False), get_storage)
