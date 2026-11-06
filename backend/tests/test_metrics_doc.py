"""문서의 지표 표가 서버 설정(METRICS)과 같은지 확인한다."""

import re
from pathlib import Path

from app.config.metrics import METRICS

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


def test_eval_sentence_syllable_counts():
    rows = [r for r in table_rows((DOCS / "metrics-spec.md").read_text(encoding="utf-8")) if len(r) == 4 and r[0] in ("A", "B", "C")]

    assert len(rows) == 3
    for _, sentence, count, _ in rows:
        syllables = re.findall("[가-힣]", sentence)
        assert len(syllables) == int(count)
        assert 15 <= len(syllables) <= 25
        assert not re.search("[0-9A-Za-z]", sentence)
