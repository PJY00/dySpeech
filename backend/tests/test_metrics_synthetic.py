"""정답을 아는 합성 신호(fixtures/synthetic)로 서버 지표 계산을 확인한다."""

import json

import numpy as np
import pytest

from app.metrics import pitch, speech, syllables
from app.metrics.audio import load_wav
from app.metrics.phonation import decay_slope, longest_phonation
from app.metrics.session import RecordingInput, compute_session, frame_at
from app.metrics.vad import get_detector
from app.schemas.common import Metric, RecordingKind
from fixtures.synthetic import generate

SIGNALS = generate.load_signals()
ANSWERS = json.loads(generate.ANSWERS.read_text(encoding="utf-8"))
FILES = ANSWERS["files"]


@pytest.fixture(scope="module")
def wav_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    assert generate.main(["--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def audio(wav_dir):
    return lambda name: load_wav((wav_dir / f"{name}.wav").read_bytes())


@pytest.fixture(scope="module")
def noise(audio):
    return float(np.median(audio("noise_floor").levels))


def near(got: float, expected: dict) -> bool:
    return abs(got - expected["value"]) <= expected["tolerance"]


def check(got: float, expected: dict, name: str) -> None:
    assert near(got, expected), f"{name}: {got:.4f}, 정답 {expected['value']} ± {expected['tolerance']}"


def test_answers_file_matches_signals():
    assert generate.render_answers(SIGNALS) == generate.ANSWERS.read_text(encoding="utf-8"), (
        "signals.json이 바뀌었습니다. python fixtures/synthetic/generate.py --answers로 다시 쓰세요"
    )


def test_noise_floor(noise):
    check(noise, FILES["noise_floor"]["noise_floor"], "noise_floor")


def test_frame_levels_follow_steps(audio):
    steps_audio = audio("level_steps")
    times = steps_audio.frame_times()
    for step, expected in zip(SIGNALS["files"]["level_steps"]["steps"], FILES["level_steps"]["step_levels_dbfs"], strict=True):
        inside = (times > step["start_s"] + 0.1) & (times < step["end_s"] - 0.1)
        check(float(np.median(steps_audio.levels[inside])), expected, f"step {step['level_dbfs']}")


@pytest.mark.parametrize("name", ["broken_phonation", "steady_phonation"])
def test_sustained_phonation(audio, noise, name):
    phonation = longest_phonation(audio(name), noise)

    check(phonation.duration_s, FILES[name]["max_phonation_time"], "max_phonation_time")
    check(phonation.median_level, FILES[name]["sustained_level"], "sustained_level")
    check(decay_slope(phonation), FILES[name]["loudness_decay_slope"], "loudness_decay_slope")


def test_calibration_and_intonation_on_glide(audio, noise):
    glide = audio("f0_glide")
    expected = FILES["f0_glide"]

    low, high = pitch.calibration_range(glide.samples)
    check(low, expected["calibration_low_hz"], "calibration_low_hz")
    check(high, expected["calibration_high_hz"], "calibration_high_hz")
    phonation = longest_phonation(glide, noise)
    mask = np.zeros(len(glide.levels), dtype=bool)
    mask[phonation.start : phonation.end] = True
    tone = pitch.intonation(pitch.extract(glide.samples, 75, 500), lambda t: mask[frame_at(t, len(mask))])
    check(tone.f0_median, expected["f0_median"], "f0_median")
    check(tone.f0_sd_semitones, expected["f0_sd_semitones"], "f0_sd_semitones")
    check(tone.pitch_range_semitones, expected["pitch_range_semitones"], "pitch_range_semitones")
    assert not tone.low_reliability


@pytest.mark.parametrize("name", ["syllable_train", "paused_speech"])
def test_reading(audio, noise, name):
    reading_audio = audio(name)
    expected = FILES[name]
    reading = speech.find_reading(reading_audio, noise, get_detector().probabilities(reading_audio.samples))

    check(reading.total_s, expected["total_s"], "total_s")
    check(reading.pause_s, expected["pause_s"], "pause_s")
    for metric, value in speech.rates(reading, 21).items():
        check(value, expected[metric], metric)
    mask = reading.mask(len(reading_audio.levels))
    tone = pitch.intonation(pitch.extract(reading_audio.samples, 75, 500), lambda t: mask[frame_at(t, len(mask))])
    for metric in ("f0_median", "f0_sd_semitones", "pitch_range_semitones"):
        check(getattr(tone, metric), expected[metric], metric)
    detected = syllables.count_nuclei(reading_audio.samples)
    check(detected, expected["detected_syllables"], "detected_syllables")
    assert not syllables.possible_reading_error(detected, 21)


def test_inner_short_gap_is_not_a_pause(audio, noise):
    reading_audio = audio("paused_speech")
    reading = speech.find_reading(reading_audio, noise, get_detector().probabilities(reading_audio.samples))

    # 구절 사이 휴지 2개만 남고, 첫 구절 안의 0.15초 끊김은 이어 붙인다.
    assert len(reading.segments) == 3


def test_louder_reading_changes_sentence_level_by_gain(wav_dir, noise):
    def sentence_level(name: str) -> float:
        result = compute_session([RecordingInput(RecordingKind.eval_sentence, (wav_dir / f"{name}.wav").read_bytes, "s1")])
        return result.sentence_level

    gain = sentence_level("paused_speech_louder") - sentence_level("paused_speech")
    check(gain, FILES["paused_speech_louder"]["mean_level_change_from_paused_speech"], "mean_level_change")


def test_whole_session_matches_session_answers(wav_dir):
    recordings = [
        RecordingInput(RecordingKind(item["kind"]), (wav_dir / f"{item['file']}.wav").read_bytes, item.get("sentence_id"))
        for item in ANSWERS["session"]["recordings"]
    ]
    expected = ANSWERS["session"]["answers"]

    result = compute_session(recordings)

    values = {v.metric: v for v in result.values}
    for metric in Metric:
        if metric is Metric.mean_level_change:
            continue  # 기준선 회차와 빼는 값이라 처리 작업에서 구한다
        assert metric in values, f"{metric}가 계산되지 않았습니다"
        check(values[metric].value, expected[metric.value], metric.value)
        assert not values[metric].low_reliability, metric
    check(result.calibration[0], expected["calibration_low_hz"], "calibration_low_hz")
    check(result.calibration[1], expected["calibration_high_hz"], "calibration_high_hz")
    assert values[Metric.max_phonation_time].details == {"trial": 1}
    reading_details = values[Metric.articulation_rate].details
    assert set(reading_details) == {"total_s", "pause_s", "detected_syllables", "expected_syllables"}
    assert reading_details["expected_syllables"] == 21
    assert result.notes == []
