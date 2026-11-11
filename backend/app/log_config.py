import logging
import sys

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"
HANDLER_NAME = "dyspeech-stdout"


class StdoutHandler(logging.StreamHandler):
    """쓰는 순간의 sys.stdout에 쓴다. 테스트 도구가 표준 출력을 바꿔 끼워도 따라간다."""

    def __init__(self) -> None:
        """
        표준 출력에 쓰는 로그 처리기를 초기화함
        입력: 없음
        출력: 없음
        알고리즘
        1. 지금의 sys.stdout으로 StreamHandler를 초기화함. 실제로 쓸 출력은 stream 속성이 매번 다시 정함
        """
        super().__init__(sys.stdout)

    @property
    def stream(self):
        """
        로그를 쓰는 순간의 sys.stdout을 돌려줌
        입력: 없음
        출력: 지금 sys.stdout. 테스트 도구가 표준 출력을 바꿔 끼워도 따라감
        알고리즘
        1. 저장해 둔 출력 대신 매번 sys.stdout을 돌려줌
        """
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        """
        stream 속성에 다른 출력을 넣으려는 것을 무시함
        입력: value (StreamHandler가 넣으려는 출력, 쓰지 않음)
        출력: 없음
        알고리즘
        1. 아무것도 하지 않음. 그래서 stream은 늘 지금 sys.stdout을 가리킴
        """
        pass


def configure_logging() -> None:
    """
    앱 로그(app.*)를 INFO 이상, "수준 이름: 메시지" 형식으로 표준 출력에 쓰게 함
    입력: 없음
    출력: 없음 ("app" 로거의 수준을 INFO로 두고 처리기를 붙임)
    알고리즘
    1. "app" 로거 수준을 INFO로 둠. uvicorn 로그와 접근 로그는 건드리지 않음
    2. 이름이 dyspeech-stdout인 처리기가 이미 있으면 끝냄. 그래서 여러 번 불러도 처리기는 하나만 붙음
    3. StdoutHandler를 만들어 형식(LOG_FORMAT "%(levelname)s %(name)s: %(message)s")을 정하고 로거에 붙임
    """
    logger = logging.getLogger("app")
    logger.setLevel(logging.INFO)
    if any(handler.get_name() == HANDLER_NAME for handler in logger.handlers):
        return
    handler = StdoutHandler()
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
