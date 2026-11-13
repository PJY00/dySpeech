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
        """
        처리 작업 스레드의 설정을 받아 둠
        입력: session_factory (새 DB 세션을 만드는 함수), storage_factory (녹음 저장소를 돌려주는 함수), idle_s (처리할 세션이 없을 때 기다리는 시간, 초. 기본 5초)
        출력: 없음 (멈춤·깨움 이벤트를 만들고, 스레드는 아직 시작하지 않음)
        알고리즘
        1. 받은 값을 두고 멈춤·깨움 threading.Event를 만듦. 스레드 자리는 None으로 둠
        """
        self.session_factory = session_factory
        self.storage_factory = storage_factory
        self.idle_s = idle_s
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> bool:
        """
        처리 대기 세션 하나를 꺼내 계산함
        입력: 없음
        출력: 세션 하나를 처리했으면 참, 처리할 세션이 없으면 거짓
        알고리즘
        1. 새 DB 세션을 열고 claim_next로 세션 하나를 고름(시도 횟수를 1 늘림)
        2. 없으면 거짓을 돌려줌
        3. 있으면 process_session으로 계산해 저장하고 참을 돌려줌. DB 세션은 끝나면 닫음
        """
        with self.session_factory() as db:
            session_id = claim_next(db)
            if session_id is None:
                return False
            process_session(db, self.storage_factory(), session_id)
            return True

    def _run(self) -> None:
        """
        멈춤 신호가 올 때까지 처리 대기 세션을 하나씩 계산함
        입력: 없음
        출력: 없음 (시작·멈춤을 정보 기록으로 남김)
        알고리즘
        1. 멈춤 신호가 올 때까지 run_once를 되풀이함
        2. 예외가 나면(DB 접속 끊김 등) 오류 기록만 남기고 스레드는 멈추지 않음
        3. 처리한 세션이 없거나 예외가 났으면 깨움 신호와 idle_s(기본 5초) 중 먼저 오는 것까지 기다린 뒤 깨움 신호를 지움
        """
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
        """
        처리 작업 스레드를 시작함
        입력: 없음
        출력: 없음 (데몬 스레드 "processing-worker"를 띄움)
        알고리즘
        1. _run을 도는 데몬 스레드를 만들어 시작함
        """
        self._thread = threading.Thread(target=self._run, name="processing-worker", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        """
        기다리고 있는 처리 작업을 바로 깨움
        입력: 없음
        출력: 없음
        알고리즘
        1. 깨움 이벤트를 켬. complete 엔드포인트가 세션을 처리 대기로 바꾼 뒤 부름
        """
        self._wake.set()

    def stop(self, timeout: float = 30.0) -> None:
        """
        처리 작업 스레드를 멈춤
        입력: timeout (스레드가 끝나길 기다리는 최대 시간, 초. 기본 30초)
        출력: 없음
        알고리즘
        1. 멈춤 이벤트를 켜고, 기다리는 중이면 바로 풀리도록 깨움 이벤트도 켬
        2. 스레드가 있으면 timeout초까지 끝나길 기다림
        """
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)


def default_worker() -> ProcessingWorker:
    """
    설정된 DB와 S3로 기본 처리 작업을 준비함
    입력: 없음 (DB는 환경 변수 DATABASE_URL, 녹음 저장소는 AWS_REGION·AWS_ACCESS_KEY_ID·AWS_SECRET_ACCESS_KEY·S3_BUCKET. app/settings.py로 읽음)
    출력: ProcessingWorker(부를 때마다 새 DB 세션(커밋 뒤에도 객체 값을 유지)을 여는 함수, 공용 녹음 저장소를 돌려주는 get_storage)
    알고리즘
    1. get_engine으로 새 DB 세션을 여는 함수와 get_storage를 넣어 ProcessingWorker를 만듦
    """
    from app.db.engine import get_engine
    from app.storage.s3 import get_storage

    return ProcessingWorker(lambda: Session(get_engine(), expire_on_commit=False), get_storage)
