"""반복 측정 도구: 로컬 테스트 DB에 정답을 아는 서버 값을 넣고 측정 표준오차·최소 측정 가능 변화량과 반영을 확인한다."""

import json
import math
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select

from analysis import retest
from app.config.metrics import METRICS
from app.db.models import MdcValueRow, MeasurementRow, MeasurementSource, SessionRow, UserRow
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import MdcScale, Metric, Purpose, SessionStatus

START = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)  # 한국 시간 18:00
FACTOR = 1.96 * math.sqrt(2)


@pytest.fixture
def add_session(db):
    """세션 하나와 그 서버 값을 바로 넣는다. 값은 {지표 이름: 값}, {지표 이름: (값, 신뢰 낮음)} 또는 {지표 이름: (값, 신뢰 낮음, 세부 정보)}."""

    def add(
        server=None, user_id=None, day=0, hours=0.0, purpose=Purpose.training, status=SessionStatus.done,
        client_meta=None, complete_client_meta=None, version=ALGORITHM_VERSION, input_device="wired_headset",
    ):
        user_id = user_id or uuid.uuid4()
        if db.get(UserRow, user_id) is None:
            db.add(UserRow(id=user_id, created_at=START))
            db.flush()
        session_id = uuid.uuid4()
        db.add(
            SessionRow(
                id=session_id, user_id=user_id, purpose=purpose, started_at=START + timedelta(days=day, hours=hours),
                device_model="test", os_version="test", app_version="test", input_device=input_device, sample_rate=48000,
                status=status, client_meta=client_meta or {}, complete_client_meta=complete_client_meta,
            )
        )
        db.flush()
        for name, value in (server or {}).items():
            value, low, details = (*value, None)[:3] if isinstance(value, tuple) else (value, False, None)
            db.add(
                MeasurementRow(
                    session_id=session_id, metric=name, value=value, unit=METRICS[Metric(name)].unit.value,
                    source=MeasurementSource.server, algorithm_version=version, low_reliability=low, details=details,
                )
            )
        db.commit()
        return user_id, session_id

    return add


@pytest.fixture
def add_days(add_session):
    """한 화자가 날마다 한 회차씩 한 것으로 넣는다. values는 {지표 이름: [날별 값]}."""

    def add(values: dict[str, list[float]], user_id=None, first_day=0):
        user_id = user_id or uuid.uuid4()
        for day in range(max(len(series) for series in values.values())):
            add_session({name: series[day] for name, series in values.items() if day < len(series)}, user_id=user_id, day=first_day + day)
        return user_id

    return add


def run(argv, db):
    lines = []
    code = retest.main(argv, db=db, out=lines.append)
    return code, "\n".join(lines)


def result(argv, db) -> dict:
    code, text = run([*argv, "--json"], db)
    assert code == 0
    return json.loads(text)


def by_metric(data: dict) -> dict:
    return {item["metric"]: item for item in data["metrics"]}


def rows(db) -> list[MdcValueRow]:
    db.expire_all()
    return list(db.scalars(select(MdcValueRow).order_by(MdcValueRow.id)))


def test_sem_and_mdc_come_from_within_speaker_spread_even_when_days_differ(add_days, db):
    add_days({"max_phonation_time": [1.0, 2.0, 3.0]})  # 평균 2, 편차 제곱합 2
    add_days({"max_phonation_time": [4.0, 6.0]})  # 평균 5, 편차 제곱합 2
    add_days({"max_phonation_time": [100.0]})  # 하루뿐인 화자는 계산에서 빠진다

    data = result([], db)
    mpt = by_metric(data)["max_phonation_time"]

    # 개인 내 평균제곱 = (2 + 2) ÷ ((3 − 1) + (2 − 1)) = 4 ÷ 3
    sem = math.sqrt(4 / 3)
    assert (mpt["speakers"], mpt["values"], mpt["degrees_of_freedom"], mpt["days_per_speaker"]) == (2, 5, 3, [3, 2])
    assert mpt["mean"] == pytest.approx(3.2)
    assert mpt["sem_raw"] == pytest.approx(sem)
    assert mpt["mdc_raw"] == pytest.approx(FACTOR * sem)
    assert mpt["sd_ratio_raw"] == pytest.approx(math.sqrt(2) / 1.0)  # 표준편차 √2와 1
    # 지표는 정의 순서로 11개가 모두 나오고, 값이 없는 지표는 빈 칸이다.
    assert [item["metric"] for item in data["metrics"]] == [metric.value for metric in METRICS]
    assert by_metric(data)["speech_rate"]["mdc_raw"] is None and by_metric(data)["speech_rate"]["speakers"] == 0
    assert data["sessions"] == 6 and len(data["speakers"]) == 3


def test_equal_days_match_the_balanced_formula(add_days, db):
    table = np.array([[5.0, 5.4, 5.2], [6.1, 5.9, 6.3], [4.0, 4.5, 4.1], [7.0, 6.6, 6.8]])
    for series in table:
        add_days({"articulation_rate": list(series)})

    rate = by_metric(result([], db))["articulation_rate"]

    # 날 수가 같으면 분모가 n(화자 수) × (k(날 수) − 1) = 4 × 2
    balanced = math.sqrt(((table - table.mean(axis=1, keepdims=True)) ** 2).sum() / (4 * (3 - 1)))
    assert rate["degrees_of_freedom"] == 8
    assert rate["sem_raw"] == pytest.approx(balanced)
    assert rate["mdc_raw"] == pytest.approx(FACTOR * balanced)


def test_log_scale_is_a_percentage_and_needs_positive_values(add_days, db):
    add_days({
        "articulation_rate": [1.0, math.exp(0.2)],  # 자연로그 0, 0.2 → 편차 제곱합 0.02
        "pause_ratio": [0.0, 10.0],
        "loudness_decay_slope": [-1.0, -2.0],
    })
    add_days({"articulation_rate": [1.0, math.exp(0.2), math.exp(0.4)]})  # 자연로그 0, 0.2, 0.4 → 편차 제곱합 0.08

    data = by_metric(result([], db))

    sem_log = math.sqrt((0.02 + 0.08) / 3)
    assert data["articulation_rate"]["sem_log"] == pytest.approx(sem_log)
    assert data["articulation_rate"]["mdc_log_pct"] == pytest.approx((math.exp(FACTOR * sem_log) - 1) * 100)
    assert data["articulation_rate"]["sd_ratio_log"] == pytest.approx(0.2 / math.sqrt(0.02))
    # 0이나 음수가 있으면 로그 척도는 계산하지 않고 원 척도만 낸다.
    for name in ("pause_ratio", "loudness_decay_slope"):
        assert data[name]["mdc_raw"] is not None
        assert (data[name]["sem_log"], data[name]["mdc_log_pct"], data[name]["sd_ratio_log"]) == (None, None, None)


def test_only_the_first_session_of_each_korean_day_is_used(add_session, db):
    user_id, _ = add_session({"max_phonation_time": 10.0})  # 한국 시간 11/20 18:00
    add_session({"max_phonation_time": 99.0}, user_id=user_id, hours=5.5)  # 11/20 23:30, 같은 날이라 빠진다
    add_session({"max_phonation_time": 12.0}, user_id=user_id, hours=6.5)  # 11/21 00:30, 다음 날

    data = result(["--detail"], db)

    assert data["excluded"]["later_same_day"] == 1
    assert data["speakers"][0]["days"] == ["2026-11-20", "2026-11-21"]
    assert [visit["values"]["max_phonation_time"] for visit in data["visits"]] == [10.0, 12.0]
    assert data["visits"][1]["started_at"] == "2026-11-21T00:30:00+09:00"
    assert by_metric(data)["max_phonation_time"]["sem_raw"] == pytest.approx(math.sqrt(2.0))


def test_other_sessions_and_low_reliability_values_are_left_out(add_session, db):
    user_id, _ = add_session({"max_phonation_time": 10.0, "f0_median": 120.0})
    add_session({"max_phonation_time": 12.0, "f0_median": (300.0, True)}, user_id=user_id, day=1)
    add_session({"max_phonation_time": 50.0}, user_id=user_id, day=2, client_meta={"dev": True})
    add_session({"max_phonation_time": 50.0}, user_id=user_id, day=3, complete_client_meta={"dev": True})
    add_session({"max_phonation_time": 50.0}, user_id=user_id, day=4, purpose=Purpose.retest)
    add_session({"max_phonation_time": 50.0}, user_id=user_id, day=5, status=SessionStatus.processing)
    add_session({"max_phonation_time": 50.0}, user_id=user_id, day=6, version="0.9.0")

    data = result([], db)

    assert data["excluded"] == {"dev_sessions": 2, "later_same_day": 0, "low_reliability_values": 1}
    mpt = by_metric(data)["max_phonation_time"]
    assert (mpt["values"], mpt["mean"]) == (2, 11.0)
    # 신뢰 낮음 값을 빼면 기본주파수 중앙값은 하루치만 남아 계산되지 않는다.
    assert by_metric(data)["f0_median"]["speakers"] == 0


def test_user_ids_of_one_speaker_are_merged_and_level_change_uses_the_sentence_level(add_session, db):
    first, _ = add_session({"mean_level_change": (0.0, False, {"sentence_level_dbfs": -20.0}), "max_phonation_time": 10.0})
    add_session({"mean_level_change": (2.0, False, {"sentence_level_dbfs": -18.0}), "max_phonation_time": 11.0}, user_id=first, day=1)
    # 앱을 다시 설치해 사용자 ID가 바뀌면 평균 음량 변화는 다시 0에서 시작한다.
    second, _ = add_session({"mean_level_change": (0.0, False, {"sentence_level_dbfs": -19.0}), "max_phonation_time": 12.0}, day=2)
    add_session({"mean_level_change": 5.0}, user_id=second, day=3)  # 세부 정보가 없는 값은 쓰지 않는다

    apart = result([], db)
    merged = result(["--same-speaker", f"{first}={second}", "--detail"], db)

    # 묶지 않으면 첫 사용자의 이틀(−20, −18)만 들어간다: 편차 제곱합 2, 자유도 1
    assert len(apart["speakers"]) == 2
    assert by_metric(apart)["mean_level_change"]["sem_raw"] == pytest.approx(math.sqrt(2.0))
    # 묶으면 한 화자의 사흘(−20, −18, −19): 편차 제곱합 2, 자유도 2
    assert merged["speakers"] == [
        {"speaker": str(first), "user_ids": [str(first), str(second)], "days": ["2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"],
         "input_devices": ["wired_headset"]}
    ]
    level = by_metric(merged)["mean_level_change"]
    assert (level["values"], level["degrees_of_freedom"], level["mean"]) == (3, 2, -19.0)
    assert level["sem_raw"] == pytest.approx(1.0)
    assert by_metric(merged)["max_phonation_time"]["days_per_speaker"] == [3]
    assert [visit["values"].get("mean_level_change") for visit in merged["visits"]] == [-20.0, -18.0, -19.0, None]


def test_icc_matches_a_published_example():
    # Shrout와 Fleiss(1979)의 예: 대상 6명 × 평가자 4명, ICC(2,1) = 0.29
    table = np.array([[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8], [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]], dtype=float)

    assert retest.icc_absolute_single(table) == pytest.approx(0.2898, abs=5e-4)
    assert retest.icc_absolute_single(table[:1]) is None
    assert retest.icc_absolute_single(np.ones((3, 3))) is None


def test_icc_uses_the_first_three_days_of_speakers_with_three_or_more(add_days, db):
    table = np.array([[5.0, 5.4, 5.2], [6.1, 5.9, 6.3], [4.0, 4.5, 4.1]])
    add_days({"speech_rate": [*table[0], 9.0]})  # 넷째 날은 급내상관계수에 쓰지 않는다
    add_days({"speech_rate": list(table[1])})
    add_days({"speech_rate": list(table[2])})
    add_days({"speech_rate": [5.0, 5.1]})  # 이틀뿐인 화자는 급내상관계수에서 빠진다

    rate = by_metric(result([], db))["speech_rate"]

    assert (rate["speakers"], rate["icc_speakers"]) == (4, 3)
    assert rate["icc"] == pytest.approx(retest.icc_absolute_single(table))


def four_speakers(add_days, extra: dict[str, list[float]] | None = None):
    for base in (5.0, 6.0, 7.0, 8.0):
        add_days({"max_phonation_time": [base, base + 1.0, base + 2.0], "f0_median": [100.0, 110.0, 120.0], **(extra or {})})


def test_preview_writes_nothing_and_yes_stores_progress_metrics_only(add_days, db):
    four_speakers(add_days)
    db.add(MdcValueRow(metric="max_phonation_time", value=9.0, scale=MdcScale.raw, method="old", n_speakers=1, n_repeats=1,
                       algorithm_version=ALGORITHM_VERSION, active=True))
    db.add(MdcValueRow(metric="max_phonation_time", value=8.0, scale=MdcScale.raw, method="old", n_speakers=1, n_repeats=1,
                       algorithm_version="0.9.0", active=True))
    db.commit()

    assert result([], db)["protocol_met"] is True
    assert len(rows(db)) == 2

    code, text = run(["--yes"], db)

    assert code == 0 and "mdc_values에 지표 1개의 값을 넣었습니다." in text
    stored = rows(db)
    assert [(row.metric, row.algorithm_version, row.active) for row in stored] == [
        ("max_phonation_time", ALGORITHM_VERSION, False),  # 이전 활성 값은 끈다
        ("max_phonation_time", "0.9.0", True),  # 다른 알고리즘 버전의 값은 건드리지 않는다
        ("max_phonation_time", ALGORITHM_VERSION, True),
    ]
    new = stored[-1]
    # 화자마다 (b, b + 1, b + 2): 편차 제곱합 2 × 4명, 자유도 8 → 측정 표준오차 1
    assert (new.value, new.scale, new.method, new.n_speakers, new.n_repeats) == (round(FACTOR, 4), MdcScale.raw, "within_subject_sem", 4, 3)
    assert "화자별 날 수 3·3·3·3, 값 12개, 자유도 8" in new.note
    # 기본주파수 중앙값은 보조 지표라 넣지 않는다.
    assert "f0_median" not in {row.metric for row in stored}


def test_stored_value_turns_pending_into_a_judgement(flow_client, add_days, db):
    four_speakers(add_days)
    user_id = db.scalars(select(SessionRow.user_id).order_by(SessionRow.started_at, SessionRow.id)).first()

    def status():
        response = flow_client.get(f"/v1/users/{user_id}/history")
        assert response.status_code == 200, response.text
        return {item["metric"]: item for item in response.json()["metrics"]}["max_phonation_time"]

    assert (status()["status"], status()["mdc"]) == ("pending_mdc", None)

    assert run(["--yes"], db)[0] == 0

    # 이 화자는 첫날보다 2초 늘었고 최소 측정 가능 변화량은 약 2.77초다.
    after = status()
    assert after["mdc"] == {"value": round(FACTOR, 4), "scale": "raw"}
    assert (after["latest"]["change"], after["status"]) == (2.0, "no_change")


def test_log_option_stores_the_percentage_and_rejects_wrong_metrics(add_days, db):
    four_speakers(add_days, {"articulation_rate": [5.0, 5.5, 5.2], "pause_ratio": [0.0, 5.0, 3.0]})

    data = result(["--log", "articulation_rate"], db)
    picked = {item["metric"]: item for item in data["selected"]}

    assert list(picked) == ["max_phonation_time", "articulation_rate", "pause_ratio"]
    assert picked["articulation_rate"]["scale"] == "log"
    assert picked["articulation_rate"]["value"] == round(by_metric(data)["articulation_rate"]["mdc_log_pct"], 4)
    assert picked["max_phonation_time"]["scale"] == "raw"

    code, text = run(["--log", "pause_ratio", "--yes"], db)
    assert code == 2 and "pause_ratio" in text and "로그 척도로 계산되지 않았습니다" in text
    code, text = run(["--log", "f0_median,nothing", "--yes"], db)
    assert code == 2 and "진전도 지표만" in text and "f0_median, nothing" in text
    assert rows(db) == []

    assert run(["--log", "articulation_rate", "--yes"], db)[0] == 0
    assert {(row.metric, row.scale) for row in rows(db)} == {
        ("max_phonation_time", MdcScale.raw), ("articulation_rate", MdcScale.log), ("pause_ratio", MdcScale.raw),
    }


def test_a_metric_that_never_varied_is_not_stored(add_days, db):
    four_speakers(add_days, {"pause_ratio": [0.0, 0.0, 0.0]})

    code, text = run(["--yes"], db)

    # 측정 표준오차가 0이면 최소 측정 가능 변화량도 0이라, 넣으면 아주 작은 차이도 변화로 판정된다.
    assert code == 0
    pause = next(line for line in text.splitlines() if line.startswith("| pause_ratio |"))
    assert "| 0.000 | 0.000 |" in pause and pause.endswith("| 변동 없음(반영 안 함) |")
    assert [row.metric for row in rows(db)] == ["max_phonation_time"]


def test_values_are_not_stored_until_four_speakers_have_three_days_unless_forced(add_days, db):
    for base in (5.0, 6.0, 7.0):
        add_days({"max_phonation_time": [base, base + 1.0, base + 2.0]})
    add_days({"max_phonation_time": [8.0, 9.0]})

    assert result([], db)["protocol_met"] is False
    code, text = run(["--yes"], db)

    assert code == 1 and "조건(4명 × 서로 다른 날 3회) 미충족" in text and "--force" in text
    assert rows(db) == []

    assert run(["--yes", "--force"], db)[0] == 0
    assert [(row.metric, row.n_speakers, row.n_repeats) for row in rows(db)] == [("max_phonation_time", 4, 2)]


def test_markdown_tables(add_session, add_days, db):
    four_speakers(add_days, {"sustained_level": [-20.0, -21.0, -19.0]})
    user_id, session_id = add_session({"max_phonation_time": 9.0}, day=0, input_device="built_in_mic")
    add_session({"max_phonation_time": 9.5}, user_id=user_id, day=1, input_device="wired_headset")

    code, text = run(["--detail"], db)

    assert code == 0
    assert f"서버 알고리즘 버전 {ALGORITHM_VERSION}" in text and "회차 14개, 화자 5명. 조건(4명 × 서로 다른 날 3회) 충족" in text
    assert f"| {str(user_id)[:8]} | {str(user_id)[:8]} | 2 | 11/20, 11/21 | built_in_mic, wired_headset |" in text
    mpt = next(line for line in text.splitlines() if line.startswith("| max_phonation_time |"))
    assert mpt.startswith("| max_phonation_time | s | 5 | 14 | 9 |") and mpt.endswith("s (raw) |")
    level = next(line for line in text.splitlines() if line.startswith("| sustained_level |"))
    assert level.endswith("| 보조(반영 안 함) |") and "| -20.000 |" in level
    detail = next(line for line in text.splitlines() if str(session_id) in line)
    assert detail.startswith(f"| {str(user_id)[:8]} | 2026-11-20 18:00 | {session_id} | built_in_mic | 9.000 | - |")

    assert "| 화자 | 시작(한국 시간) |" not in run([], db)[1]


def test_no_sessions_is_reported_without_tables(db):
    code, text = run([], db)

    assert code == 0 and "계산할 회차가 없습니다." in text and "| 지표 |" not in text
    assert run(["--yes"], db) == (1, text + "\n반영할 값이 없습니다.")


def test_same_speaker_option_must_name_two_distinct_groups(db):
    a, b, c = (uuid.uuid4() for _ in range(3))

    with pytest.raises(SystemExit):
        retest.main(["--same-speaker", str(a)], db=db)
    with pytest.raises(SystemExit):
        retest.main(["--same-speaker", f"{a}=여기"], db=db)
    code, text = run(["--same-speaker", f"{a}={b}", "--same-speaker", f"{c}={b}"], db)
    assert code == 2 and str(b) in text
