"""자동 게인 조절 시험 소리가 signals.json의 정답값대로 만들어지는지 확인한다."""

import wave

import numpy as np
import pytest

from fixtures.agc import generate

SIGNALS = generate.load_signals()
RATE = SIGNALS["format"]["sample_rate"]
RAMP_S = SIGNALS["ramp_ms"] / 1000


@pytest.fixture(scope="module")
def wav_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("agc")
    assert generate.main(["--out", str(out)]) == 0
    return out


def read(wav_dir, name: str) -> np.ndarray:
    with wave.open(str(wav_dir / f"{name}.wav"), "rb") as file:
        assert (file.getnchannels(), file.getsampwidth(), file.getframerate()) == (1, 2, RATE)
        frames = file.readframes(file.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768


def level_dbfs(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x**2)))


def segment_body(x: np.ndarray, segment: dict) -> np.ndarray:
    """앞뒤로 소리를 키우고 줄이는 부분을 뺀 구간"""
    return x[round((segment["start_s"] + RAMP_S) * RATE) : round((segment["end_s"] - RAMP_S) * RATE)]


@pytest.mark.parametrize("name", SIGNALS["files"])
def test_wav_follows_signals_json(wav_dir, name):
    spec = SIGNALS["files"][name]
    x = read(wav_dir, name)

    assert len(x) == round(spec["duration_s"] * RATE)
    sound = np.zeros(len(x), dtype=bool)
    for segment in spec["segments"]:
        assert level_dbfs(segment_body(x, segment)) == pytest.approx(segment["level_dbfs"], abs=0.05)
        sound[round(segment["start_s"] * RATE) : round(segment["end_s"] * RATE)] = True
    assert not x[~sound].any()


@pytest.mark.parametrize("name", SIGNALS["files"])
def test_tone_is_220_hz_without_clipping(wav_dir, name):
    x = read(wav_dir, name)
    segment = SIGNALS["files"][name]["segments"][-1]
    body = segment_body(x, segment)

    spectrum = np.abs(np.fft.rfft(body * np.hanning(len(body))))
    peak_hz = np.argmax(spectrum) * RATE / len(body)
    assert peak_hz == pytest.approx(SIGNALS["frequency_hz"], abs=1.0)
    assert np.max(np.abs(x)) < 1.0


def test_steps_rise_by_6_db_every_3_seconds():
    segments = SIGNALS["files"]["agc_steps"]["segments"]

    assert all(s["end_s"] - s["start_s"] == 3.0 for s in segments)
    assert all(b["start_s"] == a["end_s"] for a, b in zip(segments, segments[1:]))
    assert [b["level_dbfs"] - a["level_dbfs"] for a, b in zip(segments, segments[1:])] == [6.0] * (len(segments) - 1)


def test_sustain_keeps_one_level_for_30_seconds():
    (segment,) = SIGNALS["files"]["agc_sustain"]["segments"]

    assert segment["end_s"] - segment["start_s"] == 30.0


def test_on_off_repeats_one_second_on_and_off():
    segments = SIGNALS["files"]["agc_on_off"]["segments"]

    assert all(s["end_s"] - s["start_s"] == 1.0 for s in segments)
    assert all(b["start_s"] - a["end_s"] == 1.0 for a, b in zip(segments, segments[1:]))
    assert len({s["level_dbfs"] for s in segments}) == 1
