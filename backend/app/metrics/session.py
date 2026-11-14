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
    trial_no: int | None = None


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
        """
        계산한 지표 값 하나를 결과에 더함
        입력: metric (지표 이름), value (값, 지표 단위), low_reliability (신뢰 낮음 여부, 기본 거짓), details (함께 저장할 세부 정보, 이름=값)
        출력: 없음 (self.values에 MetricValue를 추가함)
        알고리즘
        1. 값을 float으로 바꿔 MetricValue를 만들고 목록 끝에 붙임
        """
        self.values.append(MetricValue(metric, float(value), low_reliability, details))


def frame_at(times: np.ndarray, count: int) -> np.ndarray:
    """
    시각(초)에 가장 가까운 음량 프레임 번호를 구함
    입력: times (시각 배열, 초), count (음량 프레임 수)
    출력: 프레임 번호 배열(0 ~ count − 1)
    알고리즘
    1. 번호 = 반올림((시각 × 16000 Hz − 240표본(프레임 길이의 절반)) ÷ 160표본(프레임 간격))
    2. 0과 count − 1 사이로 자름
    """
    return np.clip(np.round((times * RATE - FRAME / 2) / HOP).astype(int), 0, count - 1)


def first(recordings: list[RecordingInput], kind: RecordingKind) -> RecordingInput | None:
    """
    녹음 목록에서 주어진 종류의 첫 녹음을 찾음
    입력: recordings (녹음 입력 목록), kind (녹음 종류)
    출력: 그 종류의 첫 녹음. 없으면 None
    알고리즘
    1. 목록 순서대로 보며 종류가 같은 첫 항목을 돌려줌
    """
    return next((r for r in recordings if r.kind is kind), None)


def compute_session(
    recordings: list[RecordingInput],
    calibration: tuple[float, float] | None = None,
    detector: SpeechDetector | None = None,
) -> SessionMetrics:
    """
    한 세션의 녹음에서 서버 지표를 계산함
    입력: recordings (이 세션에서 올라온 녹음: 종류, 바이트를 읽는 함수, sentence_id, trial_no), calibration (저장된 편한 음높이 범위 (최저, 최고), Hz. 이 세션에 캘리브레이션 값이 없을 때 씀), detector (발화 검출기. 없으면 공용 검출기)
    출력: SessionMetrics(지표 값 목록, 평가 문장 발화 음량(dBFS), 이 세션 캘리브레이션 범위(Hz), 참고 메모). 평균 음량 변화는 기준 회차가 있어야 하므로 여기서 구하지 않고, 처리 작업이 평가 문장 발화 음량을 기준 회차 값과 뺌
    알고리즘
    1. 잡음 바닥을 쓰는 측정 녹음을 고름: mpt_trial 녹음들, sentence_id가 서버 설정에 있는 첫 eval_sentence 녹음
    2. 첫 noise_floor 녹음이 있으면 프레임 음량 중앙값을 잡음 바닥(dBFS)으로 하고 noise_floor 지표로 더함. 없으면 녹음마다 그 녹음 프레임 음량의 하위 10백분위를 잡음 바닥으로 쓰고, 1단계의 측정 녹음이 하나라도 있을 때만 메모를 남김(자동 게인 조절 시험 세션처럼 측정 녹음이 없으면 대체 값을 쓰지 않으므로 남기지 않음)
    3. 첫 calibration_glide 녹음이 있으면 편한 음높이 범위를 구해 결과에 둠. 못 구하면 메모를 남김
    4. mpt_trial 녹음들로 최대발성지속시간, "아" 음량, 음량 감쇠 기울기를 구함(sustained)
    5. 1단계의 eval_sentence 녹음이 있으면, 이 세션 캘리브레이션(없으면 입력 calibration)으로 말속도 지표, 억양 지표, 기본주파수 중앙값, 평가 문장 발화 음량을 구함(reading)
    6. 잡음 바닥 녹음이 없었다는 표시를 4·5단계에 넘김. 녹음은 필요할 때 하나씩 읽고 버려 메모리를 아낌
    """
    result = SessionMetrics()

    trials = [r for r in recordings if r.kind is RecordingKind.mpt_trial]
    sentence = next(
        (r for r in recordings if r.kind is RecordingKind.eval_sentence and r.sentence_id in EVAL_SENTENCES), None
    )

    noise_recording = first(recordings, RecordingKind.noise_floor)
    noise_floor = None
    if noise_recording is not None:
        noise_floor = float(np.median(load_wav(noise_recording.load()).levels))
        result.add(Metric.noise_floor, noise_floor)
    elif trials or sentence is not None:
        result.notes.append("잡음 바닥 녹음이 없어 녹음마다 음량 하위 10백분위를 잡음 바닥으로 썼다")

    def floor_for(audio: Audio) -> float:
        """
        녹음에 쓸 잡음 바닥을 정함
        입력: audio (전처리한 녹음)
        출력: 잡음 바닥(dBFS)
        알고리즘
        1. 세션의 잡음 바닥 녹음 값이 있으면 그 값을, 없으면 이 녹음 프레임 음량의 10백분위를 돌려줌
        """
        return noise_floor if noise_floor is not None else float(np.percentile(audio.levels, FALLBACK_NOISE_PERCENTILE))

    glide = first(recordings, RecordingKind.calibration_glide)
    if glide is not None:
        result.calibration = pitch.calibration_range(load_wav(glide.load()).samples)
        if result.calibration is None:
            result.notes.append("캘리브레이션 녹음에서 음높이를 충분히 찾지 못했다")

    sustained(result, trials, floor_for, noise_floor is None)

    if sentence is not None:
        reading(result, sentence, floor_for, result.calibration or calibration, detector or get_detector(), noise_floor is None)
    return result


def sustained(result: SessionMetrics, trials: list[RecordingInput], floor_for, no_noise: bool) -> None:
    """
    "아" 시도 녹음들에서 가장 긴 발성으로 지속 발성 지표 세 가지를 구함
    입력: result (지표를 더할 결과), trials (mpt_trial 녹음 목록), floor_for (녹음을 받아 잡음 바닥(dBFS)을 돌려주는 함수), no_noise (잡음 바닥 녹음이 없었는지)
    출력: 없음 (result에 max_phonation_time(초), sustained_level(dBFS), loudness_decay_slope(dB/초)를 더함. 세부 정보 trial에 고른 시도 번호)
    알고리즘
    1. 시도마다 녹음을 읽어 가장 긴 발성 구간을 찾음. 시도 번호는 trial_no, 없으면 목록 순서(1부터)
    2. 시도 중 발성 구간이 가장 긴 것을 고름(길이가 같으면 앞 시도)
    3. 발성을 하나도 못 찾으면, 시도 녹음이 있었을 때만 메모를 남기고 끝냄
    4. 그 구간 길이를 max_phonation_time, 발성 프레임 음량 중앙값을 sustained_level로 더함. 신뢰 낮음 = no_noise
    5. 음량 감쇠 기울기를 구할 수 있으면 더함. 신뢰 낮음 = no_noise 또는 발성이 2초 미만
    """
    best = None
    for index, trial in enumerate(trials, start=1):
        trial_no = trial.trial_no or index
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
    """
    평가 문장 녹음에서 말속도·억양 지표와 기본주파수 중앙값, 발화 음량을 구함
    입력:
    - result: 지표를 더할 결과
    - sentence: eval_sentence 녹음(서버 설정에 있는 sentence_id)
    - floor_for: 녹음을 받아 잡음 바닥(dBFS)을 돌려주는 함수
    - calibration: 편한 음높이 범위 (최저, 최고), Hz. 없으면 None
    - detector: 발화 검출기
    - no_noise: 잡음 바닥 녹음이 없었는지
    출력: 없음 (result에 articulation_rate·speech_rate(음절/초), pause_ratio(%), f0_sd_semitones·pitch_range_semitones(반음), f0_median(Hz)을 더하고 sentence_level(평가 문장 발화 음량, dBFS)을 둠. 못 구한 단계는 메모만 남김)
    알고리즘
    1. 녹음을 읽고 잡음 바닥을 정한 뒤, 발화 확률로 발화와 휴지를 나눔(find_reading). 발화가 없으면 메모를 남기고 끝냄
    2. 정답 음절 수(서버 설정)와 검출 음절 수(count_nuclei)를 구함. 검출 음절 수는 저장만 하고, "읽기 오류 가능" 표시는 검출 음절 수 오차율을 확인할 때까지 끔
    3. 말속도 지표 세 가지를 정답 음절 수로 구해 더함. 신뢰 낮음 = no_noise, 세부 정보 = total_s·pause_s(초, 소수 셋째 자리), detected_syllables, expected_syllables
    4. 발화 구간 프레임 음량의 중앙값을 sentence_level로 둠
    5. 캘리브레이션으로 탐색 범위를 정해(search_range) 기본주파수를 구하고, 발화 구간 안의 유성 프레임으로 억양을 구함. 기본주파수 프레임 시각은 frame_at으로 가장 가까운 음량 프레임에 맞춤
    6. 유성 구간이 없으면 메모를 남기고 끝냄
    7. f0_sd_semitones, pitch_range_semitones, f0_median을 더함. 신뢰 낮음 = 유성 프레임 비율 50% 미만(no_noise는 반영하지 않음), 세부 정보 = voiced_ratio(소수 셋째 자리), search_range_hz(Hz, 소수 첫째 자리)
    """
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
