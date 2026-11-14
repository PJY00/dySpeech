"""서버 지표 계산의 규칙(경계값, 보정, 대체 처리)을 작은 입력으로 확인한다."""

import hashlib
import io
import wave

import numpy as np
import pytest

from app.metrics import pitch, speech, syllables
from app.metrics.audio import HOP_S, RATE, Audio, bridge, frame_levels, load_wav, prepare, runs
from app.metrics.phonation import longest_phonation
from app.metrics.session import RecordingInput, compute_session
from app.metrics.vad import CHUNK, MODEL_PATH, MODEL_SHA256, get_detector
from app.schemas.common import Metric, RecordingKind


def wav_bytes(samples: np.ndarray, rate: int = 48000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(np.round(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def sine(seconds: float, hz: float = 1000.0, dbfs: float = -20.0, rate: int = 48000) -> np.ndarray:
    t = np.arange(round(seconds * rate)) / rate
    return np.sqrt(2) * 10 ** (dbfs / 20) * np.sin(2 * np.pi * hz * t)


def levels_audio(levels: list[float]) -> Audio:
    return Audio(np.zeros(len(levels) * 160 + 320), np.asarray(levels, dtype=float))


# ---- 전처리 ----


@pytest.mark.parametrize("dbfs", [-40.0, -20.0, -6.0])
def test_frame_level_of_a_sine_is_its_rms_in_dbfs(dbfs):
    levels = frame_levels(sine(1.0, dbfs=dbfs, rate=RATE))

    assert np.allclose(levels, dbfs, atol=0.05)


def test_silence_level_is_floored_and_short_input_has_no_frames():
    assert np.allclose(frame_levels(np.zeros(RATE)), -200)
    assert len(frame_levels(np.zeros(100))) == 0


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_resampling_and_highpass_keep_level(rate):
    audio = prepare(sine(1.0, dbfs=-20, rate=rate), rate)

    assert abs(audio.duration_s - 1.0) < 0.001
    assert abs(np.median(audio.levels) + 20) < 0.1


def test_highpass_removes_hum():
    hum = prepare(sine(1.0, hz=30, dbfs=-20, rate=RATE), RATE)

    assert np.median(hum.levels) < -40


def test_runs_and_bridge():
    mask = np.array([0, 1, 1, 0, 0, 1, 0, 0, 0, 1], dtype=bool)

    assert runs(mask) == [(1, 3), (5, 6), (9, 10)]
    assert bridge(runs(mask), 2) == [(1, 6), (9, 10)]
    assert bridge(runs(mask), 3) == [(1, 10)]


# ---- 지속 발성 ----


@pytest.mark.parametrize(("gap_frames", "bridged"), [(20, True), (21, False)])
def test_phonation_gap_up_to_0_2_s_is_bridged(gap_frames, bridged):
    levels = [-60] * 10 + [-20] * 100 + [-60] * gap_frames + [-20] * 100 + [-60] * 10

    phonation = longest_phonation(levels_audio(levels), -60)

    expected = 200 + gap_frames if bridged else 100
    assert round(phonation.duration_s / HOP_S) == expected


def test_piece_shorter_than_0_1_s_is_dropped():
    levels = [-60] * 30 + [-20] * 9 + [-60] * 30

    assert longest_phonation(levels_audio(levels), -60) is None


def test_phonation_needs_10_db_over_noise_floor():
    audio = levels_audio([-60] * 10 + [-50.5] * 100 + [-60] * 10)

    assert longest_phonation(audio, -60) is None
    assert longest_phonation(audio, -61) is not None


# ---- 발화·휴지 ----


@pytest.mark.parametrize(("gap_frames", "pauses"), [(24, 0), (25, 1)])
def test_gap_of_0_25_s_or_more_is_a_pause(gap_frames, pauses):
    levels = [-60] * 20 + [-20] * 100 + [-60] * gap_frames + [-20] * 100 + [-60] * 20
    audio = levels_audio(levels)
    probabilities = np.ones(len(audio.samples) // CHUNK + 1)

    reading = speech.find_reading(audio, -60, probabilities)

    assert len(reading.segments) == 1 + pauses
    assert reading.total_s == pytest.approx((200 + gap_frames) * HOP_S)
    assert reading.pause_s == pytest.approx(gap_frames * HOP_S if pauses else 0)


def test_speech_needs_both_vad_and_level():
    audio = levels_audio([-20] * 200)
    quiet_vad = np.zeros(len(audio.samples) // CHUNK + 1)

    assert speech.find_reading(audio, -60, quiet_vad) is None
    assert speech.find_reading(levels_audio([-55] * 200), -60, quiet_vad + 1) is None


def test_rates_satisfy_the_identity():
    reading = speech.Reading(0, 500, [(0, 200), (260, 500)])

    rates = speech.rates(reading, 21)

    assert rates["speech_rate"] == pytest.approx(rates["articulation_rate"] * (1 - rates["pause_ratio"] / 100))
    assert rates["pause_ratio"] == pytest.approx(60 / 500 * 100)


# ---- 기본주파수 ----


def test_clean_trims_edges_folds_octaves_and_filters():
    hz = np.full(30, np.nan)
    hz[5:25] = 200.0
    hz[12] = 400.0  # 옥타브 위 오류 → 접는다
    hz[15] = 900.0  # 접어도 멀다 → 버린다

    cleaned = pitch.clean(hz)

    assert np.isnan(cleaned[5:7]).all() and np.isnan(cleaned[23:25]).all()
    assert cleaned[12] == pytest.approx(200.0)
    assert np.isnan(cleaned[15])  # 버린 자리는 비워 둔다. 중앙값 필터는 유성 구간 안에서만 건다
    assert np.nanmax(cleaned) == pytest.approx(200.0)


@pytest.mark.parametrize(
    ("calibration", "expected"),
    [
        (None, (75.0, 500.0)),
        ((124.6, 295.0), (75.0, 354.0)),  # 아래쪽은 75 Hz보다 올리지 않는다
        ((80.0, 200.0), (64.0, 240.0)),
        ((40.0, 700.0), (50.0, 800.0)),  # 한계
        ((150.0, 160.0), (75.0, 192.0)),
    ],
)
def test_search_range(calibration, expected):
    assert pitch.search_range(calibration) == pytest.approx(expected)


def test_low_voiced_ratio_is_low_reliability():
    assert pitch.Intonation(1.0, 3.0, 120.0, 0.49).low_reliability
    assert not pitch.Intonation(1.0, 3.0, 120.0, 0.5).low_reliability


@pytest.mark.parametrize(("detected", "flag"), [(15, False), (14, True), (27, False), (28, True)])
def test_reading_error_flag_at_30_percent(detected, flag):
    assert syllables.possible_reading_error(detected, 21) is flag


# ---- 발화 검출 모델 ----


def test_vad_model_file_is_the_pinned_one():
    assert hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == MODEL_SHA256


def test_vad_gives_one_probability_per_chunk_and_low_for_tone():
    probabilities = get_detector().probabilities(prepare(sine(1.0, hz=220), 48000).samples)

    assert len(probabilities) == int(np.ceil(RATE / CHUNK))
    assert probabilities.max() < 0.5


# ---- 회차 계산의 대체 처리 ----


def test_without_noise_recording_values_are_low_reliability():
    tone = np.concatenate([np.zeros(24000), sine(3.0, hz=200), np.zeros(24000)]) + 1e-4 * np.random.default_rng(0).standard_normal(48000 * 4)

    result = compute_session([RecordingInput(RecordingKind.mpt_trial, lambda: wav_bytes(tone))])

    values = {v.metric: v for v in result.values}
    assert Metric.noise_floor not in values
    assert values[Metric.max_phonation_time].low_reliability
    # 앞뒤가 갑자기 시작·끝나는 소리라 필터의 과도 응답만큼 조금 길게 잡힌다.
    assert values[Metric.max_phonation_time].value == pytest.approx(3.0, abs=0.1)
    assert "잡음 바닥 녹음이 없어" in result.notes[0]


@pytest.mark.parametrize(
    "recordings",
    [
        [RecordingInput(RecordingKind.agc_tone, lambda: wav_bytes(sine(1.0, hz=1000)))],
        [RecordingInput(RecordingKind.game, lambda: wav_bytes(sine(1.0, hz=200)))],
        [RecordingInput(RecordingKind.eval_sentence, lambda: b"", "s9")],
        [],
    ],
    ids=["agc_tone", "game", "unknown_sentence", "empty"],
)
def test_without_measurement_recordings_there_is_no_noise_floor_note(recordings):
    # 잡음 바닥 대체 값은 mpt_trial과 서버 설정에 있는 eval_sentence에만 쓴다. 그런 녹음이 없으면 안내도 없다.
    result = compute_session(recordings)

    assert result.values == []
    assert result.notes == []


def test_short_phonation_slope_is_low_reliability():
    noise = 1e-3 * np.random.default_rng(1).standard_normal(48000 * 2)
    tone = np.concatenate([np.zeros(24000), sine(1.5, hz=200), np.zeros(24000)]) + 1e-3 * np.random.default_rng(2).standard_normal(48000 * 5 // 2)

    result = compute_session(
        [RecordingInput(RecordingKind.noise_floor, lambda: wav_bytes(noise)), RecordingInput(RecordingKind.mpt_trial, lambda: wav_bytes(tone))]
    )

    values = {v.metric: v for v in result.values}
    assert values[Metric.loudness_decay_slope].low_reliability
    assert not values[Metric.max_phonation_time].low_reliability


def test_unknown_sentence_and_missing_recordings_give_no_values():
    loads = []
    recording = RecordingInput(RecordingKind.eval_sentence, lambda: loads.append(1) or b"", "s9")

    result = compute_session([recording])

    assert result.values == [] and loads == []
    assert compute_session([]).values == []


def test_tone_is_not_read_as_speech():
    noise = 1e-3 * np.random.default_rng(3).standard_normal(48000 * 2)

    result = compute_session(
        [
            RecordingInput(RecordingKind.noise_floor, lambda: wav_bytes(noise)),
            RecordingInput(RecordingKind.eval_sentence, lambda: wav_bytes(sine(2.0, hz=220)), "s1"),
        ]
    )

    assert [v.metric for v in result.values] == [Metric.noise_floor]
    assert "발화를 찾지 못했다" in result.notes[0]


def test_wav_loader_mixes_down_and_reads_any_allowed_rate():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(2)
        file.setsampwidth(2)
        file.setframerate(44100)
        stereo = np.repeat(np.round(sine(0.5, rate=44100) * 32767).astype("<i2"), 2)
        file.writeframes(stereo.tobytes())

    audio = load_wav(buffer.getvalue())

    assert abs(audio.duration_s - 0.5) < 0.001
    assert abs(np.median(audio.levels) + 20) < 0.1
