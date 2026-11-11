"""자동 게인 조절 판정: 시험 소리(fixtures/agc)를 '녹음한 것처럼' 바꿔 넣고 판정을 확인한다."""

import io
import json
import uuid
import wave
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from analysis import agc_check
from app.db.models import RecordingRow, SessionRow, UserRow
from app.schemas.common import Purpose, RecordingKind, SessionStatus
from app.storage.s3 import recording_key
from fixtures.agc import generate

SIGNALS = ("agc_steps", "agc_hold", "agc_toggle")
START = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    out = tmp_path_factory.mktemp("agc")
    assert generate.main(["--out", str(out)]) == 0
    result = {}
    for name in SIGNALS:
        with wave.open(str(out / f"{name}.wav"), "rb") as file:
            rate = file.getframerate()
            result[name] = np.frombuffer(file.readframes(file.getnframes()), "<i2").astype(float) / 32768
    return result, rate


def to_wav(x: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(np.round(np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def simulated_agc(x: np.ndarray, rate: int, target_db=-20.0, max_gain_db=20.0, attack_s=0.05, release_s=1.0) -> np.ndarray:
    """폰 자동 게인 조절 흉내(10 ms 묶음): 음량이 목표보다 크면 빨리 줄이고, 작으면 천천히 키운다."""
    block = rate // 100
    count = len(x) // block
    rms = np.sqrt(np.mean(x[: count * block].reshape(count, block) ** 2, axis=1)) + 1e-6
    want = np.clip(10 ** (target_db / 20) / rms, 10 ** (-max_gain_db / 20), 10 ** (max_gain_db / 20))
    gains, current = np.empty(count), 1.0
    for i, w in enumerate(want):
        tau = attack_s if w < current else release_s
        current += (w - current) * (1 - np.exp(-0.01 / tau))
        gains[i] = current
    return x[: count * block] * np.repeat(gains, block)


def recorded(x, rate, *, lead_s=0.37, gain_db=0.0, noise_db=-70.0, agc=False, drift_db_per_s=0.0) -> bytes:
    """재생한 시험 소리를 녹음한 것처럼: 녹음 시작 뒤 재생(앞 무음), 재생 음량, 마이크 잡음, 기기 처리"""
    x = np.concatenate([np.zeros(int(lead_s * rate)), x]) * 10 ** (gain_db / 20)
    x = x + 10 ** (noise_db / 20) * np.random.default_rng(0).standard_normal(len(x))
    if drift_db_per_s:
        x = x * 10 ** (drift_db_per_s * np.arange(len(x)) / rate / 20)
    if agc:
        x = simulated_agc(x, rate)
    return to_wav(x, rate)


def recordings(tones, names=SIGNALS, **options) -> dict:
    data, rate = tones
    return {name: (lambda body=recorded(data[name], rate, **options): body) for name in names}


def by_signal(report) -> dict:
    return {r.signal: r for r in report.signals}


def test_clean_recordings_show_no_agc(tones):
    report = agc_check.analyze(recordings(tones), "test")

    assert report.verdict == "no_agc", agc_check.render(report)
    results = by_signal(report)
    assert all(r.status == "ok" for r in results.values())
    assert all(abs(d - 6.0) < 0.1 for d in results["agc_steps"].measurements["increments_db"].values())
    assert len(results["agc_steps"].measurements["increments_db"]) == 6
    assert results["agc_hold"].measurements["range_db"] < 0.2
    assert results["agc_toggle"].measurements["on_count"] == 10


def test_fast_agc_is_suspected_by_steps_and_toggle(tones):
    report = agc_check.analyze(recordings(tones, agc=True), "test")

    results = by_signal(report)
    assert report.verdict == "agc_suspected"
    assert results["agc_steps"].status == "suspected"
    assert results["agc_toggle"].status == "suspected"
    assert "자동 게인 조절 의심" in report.message


def test_slow_drift_is_suspected_by_hold(tones):
    report = agc_check.analyze(recordings(tones, names=("agc_hold",), drift_db_per_s=-0.2), "test")

    hold = by_signal(report)["agc_hold"]
    assert hold.status == "suspected"
    assert hold.measurements["range_db"] == pytest.approx(0.2 * 27, abs=0.5)


def test_missing_sound_is_reported_and_blocks_a_clean_verdict(tones):
    report = agc_check.analyze(recordings(tones, names=("agc_steps", "agc_hold")), "test")

    assert report.verdict == "undetermined"
    assert by_signal(report)["agc_toggle"].status == "missing"
    assert "agc_toggle" in report.message


def test_clipped_steps_are_left_out(tones):
    report = agc_check.analyze(recordings(tones, names=("agc_steps",), gain_db=12.0), "test")

    steps = by_signal(report)["agc_steps"]
    assert steps.status == "ok"
    assert any("찌그러짐" in reason for reason in steps.reasons)
    assert "6→7" not in steps.measurements["increments_db"]


def test_steps_buried_in_noise_are_left_out(tones):
    # 30 dB 작게 녹음: 1·2단계(−75·−69 dBFS)가 잡음 바닥(약 −75 dBFS, 16 kHz로 바꾼 뒤) + 10 dB보다 작다.
    report = agc_check.analyze(recordings(tones, names=("agc_steps",), gain_db=-30.0), "test")

    steps = by_signal(report)["agc_steps"]
    assert steps.status == "ok"
    assert [reason for reason in steps.reasons if "잡음에 묻힘" in reason] == ["1단계: 잡음에 묻힘", "2단계: 잡음에 묻힘"]
    assert set(steps.measurements["increments_db"]) == {"3→4", "4→5", "5→6", "6→7"}


def test_silence_or_broken_file_is_unusable(tones):
    _, rate = tones
    silent = {"agc_steps": lambda: to_wav(1e-4 * np.random.default_rng(1).standard_normal(rate * 5), rate)}
    broken = {"agc_hold": lambda: b"not a wav"}

    report = agc_check.analyze(silent | broken, "test")

    results = by_signal(report)
    assert results["agc_steps"].status == "unusable"
    assert results["agc_hold"].status == "unusable"
    assert report.verdict == "undetermined"


# ---- 저장된 세션과 명령 ----


@pytest.fixture
def agc_session(db, storage, tones):
    def add(user_id=None, day=0, names=SIGNALS, uploaded=True, **options):
        user_id = user_id or uuid.uuid4()
        if db.get(UserRow, user_id) is None:
            db.add(UserRow(id=user_id, created_at=START))
            db.flush()
        session_id = uuid.uuid4()
        db.add(
            SessionRow(
                id=session_id, user_id=user_id, purpose=Purpose.agc_test, started_at=START + timedelta(days=day),
                device_model="SM-S921N", os_version="Android 16", app_version="t", input_device="wired_headset",
                sample_rate=48000, status=SessionStatus.done,
            )
        )
        db.flush()
        for name, load in recordings(tones, names=names, **options).items():
            recording_id = uuid.uuid4()
            key = recording_key(user_id, session_id, recording_id)
            body = load()
            storage.client.put_object(Bucket=storage.bucket, Key=key, Body=body)
            db.add(
                RecordingRow(
                    id=recording_id, session_id=session_id, kind=RecordingKind.agc_tone, agc_signal=name, s3_key=key,
                    duration_s=20.0, sample_rate=48000, size_bytes=len(body), uploaded_at=START if uploaded else None,
                )
            )
        db.commit()
        return user_id, session_id

    return add


def run(argv, db=None, storage=None):
    lines = []
    code = agc_check.main(argv, db=db, storage=storage, out=lines.append)
    return code, "\n".join(lines)


def test_stored_session_is_judged(agc_session, db, storage):
    _, session_id = agc_session()

    code, text = run(["--session", str(session_id)], db, storage)

    assert code == 0
    assert "자동 게인 조절이 보이지 않는다" in text
    assert "기기 SM-S921N, 입력 wired_headset" in text


def test_user_option_uses_the_latest_agc_session(agc_session, db, storage):
    user_id, _ = agc_session()
    agc_session(user_id, day=3, agc=True)

    code, text = run(["--user", str(user_id), "--json"], db, storage)

    assert code == 0
    assert json.loads(text)["verdict"] == "agc_suspected"


def test_recordings_not_confirmed_by_complete_are_missing(agc_session, db, storage):
    _, session_id = agc_session(uploaded=False)

    code, text = run(["--session", str(session_id), "--json"], db, storage)

    report = json.loads(text)
    assert report["verdict"] == "undetermined"
    assert {s["status"] for s in report["signals"]} == {"missing"}


def test_unknown_session_is_an_input_error(db, storage):
    assert run(["--session", str(uuid.uuid4())], db, storage)[0] == 2


def test_file_mode(tmp_path, tones):
    paths = []
    for name, load in recordings(tones).items():
        path = tmp_path / f"{name}.wav"
        path.write_bytes(load())
        paths += ["--file", f"{name}={path}"]

    code, text = run(paths)

    assert code == 0
    assert "결과: 자동 게인 조절이 보이지 않는다" in text


def test_bad_file_arguments(tmp_path):
    assert run(["--file", f"agc_steps={tmp_path / 'none.wav'}"])[0] == 2
    with pytest.raises(SystemExit) as error:
        run(["--file", "agc_unknown=a.wav"])
    assert error.value.code == 2


def test_recording_stopped_before_the_end_is_aligned_from_the_start(tones):
    data, rate = tones
    cut = data["agc_steps"][: int(20.5 * rate)]  # 마지막 단계 중간에 녹음을 멈춤
    report = agc_check.analyze({"agc_steps": lambda: recorded(cut, rate)}, "test")

    steps = by_signal(report)["agc_steps"]
    assert steps.status == "ok"
    assert set(steps.measurements["increments_db"]) >= {"1→2", "2→3", "3→4", "4→5", "5→6"}


def test_digital_silence_files_as_generated_show_no_agc(tones):
    # 잡음이 전혀 없는, 녹음을 거치지 않은 합성 파일도 정상으로 나와야 한다.
    data, rate = tones
    report = agc_check.analyze({name: (lambda body=to_wav(data[name], rate): body) for name in SIGNALS}, "test")

    assert report.verdict == "no_agc", agc_check.render(report)
    assert all(r.measurements["noise_floor_dbfs"] == agc_check.MIN_FLOOR_DBFS for r in report.signals)
