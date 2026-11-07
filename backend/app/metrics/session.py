"""한 세션의 녹음에서 서버 지표를 계산한다. 녹음은 하나씩 읽고 버려 메모리를 아낀다.

평균 음량 변화(mean_level_change)는 기준선 회차가 있어야 하므로 여기서는 평가 문장 발화 음량만 구하고,
처리 작업이 기준선 회차의 같은 값과 뺀다.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from app.config.sentences import EVAL_SENTENCES
from app.metrics import pitch, speech, syllables
from app.metrics.audio import FRAME, HOP, RATE, Audio, load_wav
from app.metrics.phonation import SLOPE_MIN_S, decay_slope, longest_phonation
from app.metrics.vad import SpeechDetector, get_detector
from app.schemas.common import Metric, RecordingKind

FALLBACK_NOISE_PERCENTILE = 10  # 잡음 바닥 녹음이 없을 때 녹음 자체의 이 백분위를 잡음 바닥으로 쓴다


@dataclass(frozen=True)
class RecordingInput:
    kind: RecordingKind
    load: Callable[[], bytes]  # 필요할 때 읽는다
    sentence_id: str | None = None


@dataclass(frozen=True)
class MetricValue:
    metric: Metric
    value: float
    low_reliability: bool = False
    details: dict = field(default_factory=dict)


@dataclass
class SessionMetrics:
    values: list[MetricValue] = field(default_factory=list)
    # 평가 문장 발화 프레임 음량 중앙값(dBFS). 기준선 회차와 빼서 평균 음량 변화를 구한다.
    sentence_level: float | None = None
    # 이 세션의 calibration_glide에서 구한 편한 음높이 범위(Hz)
    calibration: tuple[float, float] | None = None
    notes: list[str] = field(default_factory=list)

    def add(self, metric: Metric, value: float, low_reliability: bool = False, **details) -> None:
        self.values.append(MetricValue(metric, float(value), low_reliability, details))


def frame_at(times: np.ndarray, count: int) -> np.ndarray:
    """시각(초)에 가장 가까운 음량 프레임 번호"""
    return np.clip(np.round((times * RATE - FRAME / 2) / HOP).astype(int), 0, count - 1)


def first(recordings: list[RecordingInput], kind: RecordingKind) -> RecordingInput | None:
    return next((r for r in recordings if r.kind is kind), None)


def compute_session(
    recordings: list[RecordingInput],
    calibration: tuple[float, float] | None = None,
    detector: SpeechDetector | None = None,
) -> SessionMetrics:
    result = SessionMetrics()

    noise_recording = first(recordings, RecordingKind.noise_floor)
    noise_floor = None
    if noise_recording is not None:
        noise_floor = float(np.median(load_wav(noise_recording.load()).levels))
        result.add(Metric.noise_floor, noise_floor)
    else:
        result.notes.append("잡음 바닥 녹음이 없어 녹음마다 음량 하위 10백분위를 잡음 바닥으로 썼다")

    def floor_for(audio: Audio) -> float:
        return noise_floor if noise_floor is not None else float(np.percentile(audio.levels, FALLBACK_NOISE_PERCENTILE))

    glide = first(recordings, RecordingKind.calibration_glide)
    if glide is not None:
        result.calibration = pitch.calibration_range(load_wav(glide.load()).samples)
        if result.calibration is None:
            result.notes.append("캘리브레이션 녹음에서 음높이를 충분히 찾지 못했다")

    sustained(result, [r for r in recordings if r.kind is RecordingKind.mpt_trial], floor_for, noise_floor is None)

    sentence = next(
        (r for r in recordings if r.kind is RecordingKind.eval_sentence and r.sentence_id in EVAL_SENTENCES), None
    )
    if sentence is not None:
        reading(result, sentence, floor_for, result.calibration or calibration, detector or get_detector(), noise_floor is None)
    return result


def sustained(result: SessionMetrics, trials: list[RecordingInput], floor_for, no_noise: bool) -> None:
    """시도마다 가장 긴 발성을 찾고, 그중 가장 긴 것으로 세 지표를 구한다."""
    best = None
    for trial_no, trial in enumerate(trials, start=1):
        audio = load_wav(trial.load())
        phonation = longest_phonation(audio, floor_for(audio))
        if phonation is not None and (best is None or phonation.duration_s > best[0].duration_s):
            best = (phonation, trial_no)
    if best is None:
        if trials:
            result.notes.append("최대 발성 녹음에서 발성을 찾지 못했다")
        return
    phonation, trial_no = best
    result.add(Metric.max_phonation_time, phonation.duration_s, no_noise, trial=trial_no)
    result.add(Metric.sustained_level, phonation.median_level, no_noise, trial=trial_no)
    slope = decay_slope(phonation)
    if slope is not None:
        result.add(Metric.loudness_decay_slope, slope, no_noise or phonation.duration_s < SLOPE_MIN_S, trial=trial_no)


def reading(result: SessionMetrics, sentence: RecordingInput, floor_for, calibration, detector, no_noise: bool) -> None:
    audio = load_wav(sentence.load())
    noise_floor = floor_for(audio)
    found = speech.find_reading(audio, noise_floor, detector.probabilities(audio.samples))
    if found is None:
        result.notes.append("평가 문장 녹음에서 발화를 찾지 못했다")
        return
    expected = EVAL_SENTENCES[sentence.sentence_id].syllables
    detected = syllables.count_nuclei(audio.samples)
    # 검출 음절 수는 저장만 한다. "읽기 오류 가능" 표시는 4단계에서 오차율을 확인할 때까지 끈다.
    timing = {
        "total_s": round(found.total_s, 3),
        "pause_s": round(found.pause_s, 3),
        "detected_syllables": detected,
        "expected_syllables": expected,
    }
    for name, value in speech.rates(found, expected).items():
        result.add(Metric(name), value, no_noise, **timing)

    mask = found.mask(len(audio.levels))
    result.sentence_level = float(np.median(audio.levels[mask]))

    floor, ceiling = pitch.search_range(calibration)
    track = pitch.extract(audio.samples, floor, ceiling)
    tone = pitch.intonation(track, lambda times: mask[frame_at(times, len(mask))])
    if tone is None:
        result.notes.append("평가 문장에서 유성 구간을 찾지 못했다")
        return
    info = {"voiced_ratio": round(tone.voiced_ratio, 3), "search_range_hz": [round(floor, 1), round(ceiling, 1)]}
    low = tone.low_reliability
    result.add(Metric.f0_sd_semitones, tone.f0_sd_semitones, low, **info)
    result.add(Metric.pitch_range_semitones, tone.pitch_range_semitones, low, **info)
    result.add(Metric.f0_median, tone.f0_median, low, **info)
