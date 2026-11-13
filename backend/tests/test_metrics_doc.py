"""문서의 지표 표와 평가 전용 문장이 서버 설정과 같은지 확인한다."""

import re
from pathlib import Path

from app.config.metrics import METRICS
from app.config.sentences import EVAL_SENTENCES

DOCS = Path(__file__).resolve().parents[2] / "docs"


def table_rows(text: str) -> list[list[str]]:
    return [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in text.splitlines() if line.startswith("| ")]


def code(cell: str) -> str:
    return cell.strip("`")


def test_metrics_spec_table_matches_config():
    rows = [r for r in table_rows((DOCS / "metrics-spec.md").read_text(encoding="utf-8")) if len(r) == 6 and r[2].startswith("`")]
    documented = {code(r[2]): (code(r[4]), code(r[5])) for r in rows}

    assert list(documented) == [str(m) for m in METRICS]
    assert documented == {str(m): (str(spec.unit), str(spec.desired_direction)) for m, spec in METRICS.items()}


def test_api_doc_metric_table_matches_config():
    rows = [r for r in table_rows((DOCS / "api.md").read_text(encoding="utf-8")) if len(r) == 4 and r[3] in ("진전도", "보조")]
    documented = {code(r[0]): (code(r[1]), r[3] == "진전도") for r in rows}

    assert documented == {str(m): (str(spec.unit), spec.used_for_progress) for m, spec in METRICS.items()}


def test_eval_sentence_matches_config():
    rows = [r for r in table_rows((DOCS / "metrics-spec.md").read_text(encoding="utf-8")) if len(r) == 3 and re.fullmatch(r"`s\d+`", r[0])]
    documented = {code(sentence_id): (text, int(count)) for sentence_id, text, count in rows}

    assert documented == {key: (s.text, s.syllables) for key, s in EVAL_SENTENCES.items()}
    for sentence in EVAL_SENTENCES.values():
        syllables = re.findall("[가-힣]", sentence.text)
        assert len(syllables) == sentence.syllables
        assert 15 <= sentence.syllables <= 25
        assert not re.search("[0-9A-Za-z]", sentence.text)
