"""docs/api.md의 JSON 예시가 실제 모델과 맞는지 확인한다. 예시마다 앞에 <!-- example: 모델 이름 --> 표시를 둔다."""

import json
import re
from pathlib import Path

import pytest

from app.schemas import common, history, metrics, recordings, sessions, users

DOC = Path(__file__).resolve().parents[2] / "docs" / "api.md"
EXAMPLE = re.compile(r"<!-- example: (\w+) -->\s*```json\n(.*?)```", re.S)

MODELS = {
    name: model
    for module in (common, users, sessions, recordings, history, metrics)
    for name, model in vars(module).items()
    if isinstance(model, type) and issubclass(model, common.ContractModel)
}

# 요청·응답마다 예시가 하나는 있어야 한다.
REQUIRED = {
    "UserIn",
    "User",
    "SessionIn",
    "Session",
    "UploadUrlsIn",
    "UploadUrlsOut",
    "CompleteIn",
    "CompleteAccepted",
    "SessionDetail",
    "History",
    "MetricDefinitions",
    "RecordingUrl",
    "ErrorResponse",
    "ValidationErrorResponse",
    "MissingRecordingsResponse",
}

EXAMPLES = EXAMPLE.findall(DOC.read_text(encoding="utf-8"))


def test_doc_has_an_example_for_every_request_and_response():
    assert REQUIRED <= {name for name, _ in EXAMPLES}


@pytest.mark.parametrize(("name", "body"), EXAMPLES, ids=[f"{i}-{name}" for i, (name, _) in enumerate(EXAMPLES)])
def test_doc_example_matches_model(name, body):
    MODELS[name].model_validate(json.loads(body))
