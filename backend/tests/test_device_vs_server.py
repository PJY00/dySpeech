"""기기·서버 비교 도구: 로컬 테스트 DB에 기기 값과 서버 값을 넣고 지표별 차이 요약을 확인한다."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from analysis import device_vs_server
from app.config.metrics import METRICS
from app.db.models import MeasurementRow, MeasurementSource, SessionRow, UserRow
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import Metric, Purpose, SessionStatus

START = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def add_session(db):
    """세션 하나와 그 기기 값·서버 값을 바로 넣는다. 값은 {지표 이름: 값} 또는 {지표 이름: (값, 신뢰 낮음)}."""

    def add(
        device=None, server=None, user_id=None, day=0, purpose=Purpose.training, status=SessionStatus.done,
        client_meta=None, complete_client_meta=None, version=ALGORITHM_VERSION,
    ):
        user_id = user_id or uuid.uuid4()
        if db.get(UserRow, user_id) is None:
            db.add(UserRow(id=user_id, created_at=START))
            db.flush()
        session_id = uuid.uuid4()
        db.add(
            SessionRow(
                id=session_id, user_id=user_id, purpose=purpose, started_at=START + timedelta(days=day),
                ended_at=START + timedelta(days=day, minutes=20), device_model="test", os_version="test",
                app_version="test", input_device="test", sample_rate=48000, status=status,
                client_meta=client_meta or {}, complete_client_meta=complete_client_meta,
            )
        )
        db.flush()
        for source, values in ((MeasurementSource.device, device or {}), (MeasurementSource.server, server or {})):
            for name, value in values.items():
                value, low = value if isinstance(value, tuple) else (value, False)
                db.add(
                    MeasurementRow(
                        session_id=session_id, metric=name, value=value, unit=METRICS[Metric(name)].unit.value, source=source,
                        algorithm_version=version if source is MeasurementSource.server else None, low_reliability=low,
                    )
                )
        db.commit()
        return user_id, session_id

    return add


def run(argv, db):
    lines = []
    code = device_vs_server.main(argv, db=db, out=lines.append)
    return code, "\n".join(lines)


def summary(argv, db) -> dict:
    code, text = run([*argv, "--json"], db)
    assert code == 0
    return json.loads(text)


def by_metric(data: dict) -> dict:
    return {item["metric"]: item for item in data["metrics"]}


def test_differences_are_summarised_per_metric(add_session, db):
    user_id, _ = add_session(device={"articulation_rate": 4.5, "noise_floor": -60.0}, server={"articulation_rate": 5.0, "noise_floor": -62.0})
    add_session(device={"articulation_rate": 4.0}, server={"articulation_rate": 4.0}, user_id=user_id, day=1)
    add_session(device={"articulation_rate": 6.6}, server={"articulation_rate": 6.0}, day=2)

    data = summary([], db)

    assert (data["algorithm_version"], data["sessions"], data["speakers"]) == (ALGORITHM_VERSION, 3, 2)
    rate = by_metric(data)["articulation_rate"]
    # 차이(기기 − 서버): −0.5, 0.0, +0.6
    assert (rate["unit"], rate["pairs"], rate["speakers"]) == ("syllables/s", 3, 2)
    assert rate["mean_difference"] == pytest.approx(0.1 / 3)
    assert rate["sd_difference"] == pytest.approx(0.55075705)  # 표본 표준편차(n − 1)
    assert rate["mean_absolute_difference"] == pytest.approx(1.1 / 3)
    assert rate["max_absolute_difference"] == pytest.approx(0.6)
    low, high = rate["limits_of_agreement"]
    assert low == pytest.approx(0.1 / 3 - 1.96 * 0.55075705) and high == pytest.approx(0.1 / 3 + 1.96 * 0.55075705)
    # 평균 상대 차이: (−10% + 0% + 10%) ÷ 3
    assert rate["mean_relative_difference_pct"] == pytest.approx(0.0, abs=1e-9)
    # 쌍이 하나면 표준편차와 일치 한계가 없고, dBFS는 상대 차이를 내지 않는다.
    noise = by_metric(data)["noise_floor"]
    assert (noise["pairs"], noise["mean_difference"], noise["sd_difference"], noise["limits_of_agreement"]) == (1, 2.0, None, None)
    assert noise["mean_relative_difference_pct"] is None
    # 지표 정의 순서로 나온다.
    assert [item["metric"] for item in data["metrics"]] == ["articulation_rate", "noise_floor"]


def test_only_done_sessions_with_both_values_of_the_current_version_are_paired(add_session, db):
    add_session(device={"f0_median": 130.0}, server={"f0_median": 131.0})
    add_session(device={"f0_median": 100.0}, server={"f0_median": 200.0}, status=SessionStatus.processing)
    add_session(device={"f0_median": 100.0}, server={"f0_median": 200.0}, version="0.9.0")
    add_session(device={"f0_median": 100.0})
    add_session(server={"f0_median": 200.0})
    add_session(device={"max_phonation_time": 7.0}, server={"sustained_level": -20.0})

    data = summary([], db)

    assert data["sessions"] == 1
    assert by_metric(data)["f0_median"]["mean_difference"] == pytest.approx(-1.0)
    assert list(by_metric(data)) == ["f0_median"]


def test_dev_sessions_and_low_reliability_values_are_left_out_unless_asked(add_session, db):
    add_session(device={"max_phonation_time": 7.5}, server={"max_phonation_time": 7.0})
    add_session(device={"max_phonation_time": 1.0}, server={"max_phonation_time": 9.0}, client_meta={"dev": True})
    add_session(device={"max_phonation_time": 1.0}, server={"max_phonation_time": 9.0}, complete_client_meta={"dev": True})
    add_session(device={"max_phonation_time": (3.0, True)}, server={"max_phonation_time": 5.0})
    add_session(device={"max_phonation_time": 3.0}, server={"max_phonation_time": (5.0, True)})

    data = summary([], db)

    assert data["excluded"] == {"dev_sessions": 2, "low_reliability_pairs": 2}
    assert by_metric(data)["max_phonation_time"]["pairs"] == 1

    assert by_metric(summary(["--include-dev"], db))["max_phonation_time"]["pairs"] == 3
    low = summary(["--include-low-reliability", "--detail"], db)
    assert by_metric(low)["max_phonation_time"]["pairs"] == 3
    assert low["excluded"] == {"dev_sessions": 2, "low_reliability_pairs": 0}
    assert sum(pair["low_reliability"] for pair in low["pairs"]) == 2


def test_user_session_and_purpose_filters(add_session, db):
    user_id, first = add_session(device={"sustained_level": -20.0}, server={"sustained_level": -21.0})
    add_session(device={"sustained_level": -25.0}, server={"sustained_level": -21.0}, user_id=user_id, day=1, purpose=Purpose.retest)
    add_session(device={"sustained_level": -30.0}, server={"sustained_level": -21.0}, day=2)

    assert by_metric(summary(["--user", str(user_id)], db))["sustained_level"]["pairs"] == 2
    assert by_metric(summary(["--session", str(first)], db))["sustained_level"]["mean_difference"] == pytest.approx(1.0)
    assert by_metric(summary(["--purpose", "retest"], db))["sustained_level"]["mean_difference"] == pytest.approx(-4.0)
    assert summary(["--user", str(uuid.uuid4())], db)["metrics"] == []


def test_markdown_table_and_detail_rows(add_session, db):
    _, session_id = add_session(
        device={"max_phonation_time": 7.52, "f0_median": (131.1, True)}, server={"max_phonation_time": 7.5, "f0_median": 130.8}
    )
    add_session(device={"max_phonation_time": 6.0}, server={"max_phonation_time": 6.5}, day=1)

    code, text = run(["--include-low-reliability", "--detail"], db)

    assert code == 0
    assert f"서버 알고리즘 버전 {ALGORITHM_VERSION}" in text and "세션 2개, 화자 2명" in text
    assert "| max_phonation_time | s | 2 | 2 | -0.24 | 0.37 | 0.26 | 0.50 | -0.96 ~ +0.48 | -3.7% |" in text
    assert "| f0_median | Hz | 1 | 1 | +0.30 | - | 0.30 | 0.30 | - | +0.2% |" in text
    # 세션별 값: 시작 시각 순, 한 세션 안에서는 지표 정의 순
    detail = [line for line in text.splitlines() if line.startswith(f"| {session_id}")]
    assert [line.split(" | ")[3] for line in detail] == ["max_phonation_time", "f0_median"]
    assert "| 131.10 | 130.80 | +0.30 (신뢰 낮음) |" in detail[1]

    assert "| 세션 | 시작 |" not in run([], db)[1]


def test_no_pairs_is_reported_without_a_table(add_session, db):
    add_session(device={"f0_median": 100.0})

    code, text = run([], db)

    assert code == 0
    assert "비교할 값이 없습니다." in text and "| 지표 |" not in text
    assert summary([], db) == {
        "algorithm_version": ALGORITHM_VERSION, "sessions": 0, "speakers": 0,
        "excluded": {"dev_sessions": 0, "low_reliability_pairs": 0}, "metrics": [],
    }


def test_user_and_session_cannot_be_given_together():
    with pytest.raises(SystemExit):
        device_vs_server.main(["--user", str(uuid.uuid4()), "--session", str(uuid.uuid4())])
