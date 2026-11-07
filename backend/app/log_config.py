import logging
import sys

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"
HANDLER_NAME = "dyspeech-stdout"


class StdoutHandler(logging.StreamHandler):
    """쓰는 순간의 sys.stdout에 쓴다. 테스트 도구가 표준 출력을 바꿔 끼워도 따라간다."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        pass


def configure_logging() -> None:
    """앱 로그(app.*)를 INFO 이상, "수준 이름: 메시지" 형식으로 표준 출력에 쓴다.

    uvicorn 로그와 접근 로그는 건드리지 않는다. 여러 번 불러도 처리기는 하나만 붙는다.
    """
    logger = logging.getLogger("app")
    logger.setLevel(logging.INFO)
    if any(handler.get_name() == HANDLER_NAME for handler in logger.handlers):
        return
    handler = StdoutHandler()
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
