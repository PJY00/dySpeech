import logging

from app.log_config import HANDLER_NAME, configure_logging


def our_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger("app").handlers if h.get_name() == HANDLER_NAME]


def test_configure_logging_adds_one_handler_even_if_called_twice():
    configure_logging()
    configure_logging()

    assert len(our_handlers()) == 1
    assert logging.getLogger("app").level == logging.INFO


def test_app_logs_go_to_stdout_with_level_and_name(capsys):
    configure_logging()

    logging.getLogger("app.api.auth").warning("앱 키 확인 실패: 키 불일치. GET /v1/metrics")
    logging.getLogger("app.api.auth").debug("보이지 않아야 하는 디버그 로그")

    out = capsys.readouterr().out
    assert "WARNING app.api.auth: 앱 키 확인 실패: 키 불일치. GET /v1/metrics" in out
    assert "디버그" not in out


def test_uvicorn_loggers_are_left_alone():
    configure_logging()

    assert not any(h.get_name() == HANDLER_NAME for h in logging.getLogger("uvicorn").handlers)
    assert not any(h.get_name() == HANDLER_NAME for h in logging.getLogger().handlers)
