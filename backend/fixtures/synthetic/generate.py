"""서버 지표 계산을 확인하는 합성 신호(WAV)와 정답값(answers.json)을 만든다.

numpy와 scipy만 쓴다(backend/requirements.txt에 있음). 서버 코드는 쓰지 않는다.
정답값은 signals.json의 만드는 값에서 바로 계산하므로, 서버 알고리즘과 상관없이 맞는 값이다.
    python backend/fixtures/synthetic/generate.py                이 폴더에 WAV를 만든다
    python backend/fixtures/synthetic/generate.py --out 폴더      지정한 폴더에 만든다
    python backend/fixtures/synthetic/generate.py --answers       answers.json을 다시 쓴다

WAV는 저장소에 올리지 않는다(.gitignore). 필요할 때 이 스크립트로 만든다.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, lfilter, sosfiltfilt

HERE = Path(__file__).resolve().parent
SIGNALS = HERE / "signals.json"
ANSWERS = HERE / "answers.json"
FULL_SCALE = 32767
F0_STEP_S = 0.01  # 기본주파수 정답값을 셀 때의 간격(서버 추출 간격과 같음)


def load_signals(path: Path = SIGNALS) -> dict:
    """
    합성 신호의 만드는 값(signals.json)을 읽음
    입력: path (설정 파일 경로, 기본 이 폴더의 signals.json)
    출력: 만드는 값 사전
    알고리즘
    1. 파일을 UTF-8로 읽어 JSON으로 바꿔 돌려줌
    """
    return json.loads(path.read_text(encoding="utf-8"))


class Synth:
    def __init__(self, signals: dict) -> None:
        """
        만드는 값으로 합성기를 준비함
        입력: signals (signals.json 내용)
        출력: 없음
        알고리즘
        1. 만드는 값, 샘플레이트(format.sample_rate, Hz), 대역 상한(band_limit_hz, Hz)을 저장함
        2. 앞뒤 상승·하강 길이(ramp_ms, 밀리초)를 표본 수로 바꿔 저장함
        """
        self.s = signals
        self.rate = signals["format"]["sample_rate"]
        self.band = signals["band_limit_hz"]
        self.ramp = round(signals["ramp_ms"] / 1000 * self.rate)

    # ---- 재료 ----

    def n(self, seconds: float) -> int:
        """
        초를 표본 수로 바꿈
        입력: seconds (시간, 초)
        출력: 표본 수(시간 × 샘플레이트를 반올림)
        알고리즘
        1. 시간(초) × 샘플레이트(Hz)를 반올림해 돌려줌
        """
        return round(seconds * self.rate)

    def noise(self, length: int, level_dbfs: float, seed_offset: int = 0) -> np.ndarray:
        """
        100 Hz~대역 상한의 잡음을 정한 크기로 만듦. 16 kHz로 바꿔도 크기가 그대로이도록 대역을 묶음
        입력: length (표본 수), level_dbfs (잡음 크기, dBFS. 표본 값 −1~1의 RMS(제곱평균제곱근 진폭) 기준), seed_offset (noise_seed에 더할 난수 시드 차이, 기본 0)
        출력: 잡음 표본 배열(길이 length, 크기 level_dbfs)
        알고리즘
        1. noise_seed + seed_offset으로 고정한 정규분포 난수를 앞뒤 1초씩 더 길게 만듦
        2. 100 Hz~대역 상한(7000 Hz) 4차 버터워스 대역 통과 필터를 앞뒤 두 번 걸고(위상 변화 없음), 앞뒤 1초를 버림
        3. RMS(제곱평균제곱근 진폭)가 10^(level_dbfs ÷ 20)이 되도록 크기를 맞춤
        """
        rng = np.random.default_rng(self.s["noise_seed"] + seed_offset)
        sos = butter(4, [100, self.band], "bandpass", fs=self.rate, output="sos")
        x = sosfiltfilt(sos, rng.standard_normal(length + 2 * self.rate))[self.rate : self.rate + length]
        return x * 10 ** (level_dbfs / 20) / np.sqrt(np.mean(x * x))

    def background(self, length: int) -> np.ndarray:
        """
        모든 녹음 밑에 깔리는 배경 잡음을 만들어 냄
        입력: length (표본 수)
        출력: 배경 잡음 배열(크기 background_noise_dbfs, 지금 −60 dBFS)
        알고리즘
        1. noise를 배경 잡음 크기로 부름
        """
        return self.noise(length, self.s["background_noise_dbfs"])

    def harmonic(self, f0: np.ndarray) -> np.ndarray:
        """
        배음 진폭이 1/k인 소리를 RMS(제곱평균제곱근 진폭) 1로 만듦. 배음 수가 일정하므로 크기를 식으로 맞춤
        입력: `f0` (표본마다의 기본주파수, Hz, 배열)
        출력: 표본 배열(제곱평균제곱근 진폭 1)
        알고리즘
        1. K(배음 수) = ⌊B(대역 상한, Hz) ÷ 가장 높은 기본주파수(Hz)⌋로 정함
        2. φ(위상, 라디안) = 2π × 기본주파수(Hz) 누적 합 ÷ 샘플레이트(Hz)로 구함
        3. y = Σ sin(k × φ) ÷ k (k = 1~K, k는 배음 번호)를 더함
        4. 이론상 제곱평균제곱근 진폭인 √(Σ 1 ÷ k² ÷ 2)로 나눠 1로 맞춤
        """
        count = int(self.band // np.max(f0))
        phase = 2 * np.pi * np.cumsum(f0) / self.rate
        y = sum(np.sin(k * phase) / k for k in range(1, count + 1))
        return y / np.sqrt(sum(1 / k**2 for k in range(1, count + 1)) / 2)

    def edges(self, length: int) -> np.ndarray:
        """
        앞뒤 짧은 상승·하강 곡선을 만들어 딸깍 소리를 막음
        입력: length (표본 수)
        출력: 이득 배열(가운데 1, 앞뒤 ramp_ms 동안 0에서 1로·1에서 0으로)
        알고리즘
        1. 1로 채운 배열을 만들고, 상승 길이를 ramp_ms와 길이의 절반 중 짧은 쪽으로 정함
        2. 앞쪽에 0.5 − 0.5 × cos(π × i ÷ R)(i는 표본 번호, R은 상승 표본 수) 곡선을, 뒤쪽에 그 반대 곡선을 넣음
        """
        gain = np.ones(length)
        ramp = min(self.ramp, length // 2)
        if ramp:
            curve = 0.5 - 0.5 * np.cos(np.pi * np.arange(ramp) / ramp)
            gain[:ramp] = curve
            gain[length - ramp :] = curve[::-1]
        return gain

    def tone(self, start_s: float, end_s: float, f0: np.ndarray | float, level_db: np.ndarray | float) -> tuple[int, np.ndarray]:
        """
        정한 구간에 기본주파수·크기를 따르는 배음 소리를 만들어 냄
        입력: start_s (시작, 초), end_s (끝, 초), `f0` (기본주파수, Hz. 하나의 값이나 표본마다의 배열), level_db (크기, dBFS. 하나의 값이나 표본마다의 배열)
        출력: (시작 표본 번호, 소리 표본 배열)
        알고리즘
        1. 시작·끝을 표본 번호로 바꾸고, 기본주파수와 크기를 표본마다의 배열로 넓힘
        2. 배음 소리(제곱평균제곱근 진폭 1) × 10^(크기 ÷ 20) × 앞뒤 상승·하강 곡선을 돌려줌
        """
        start, length = self.n(start_s), self.n(end_s) - self.n(start_s)
        f0 = np.broadcast_to(np.asarray(f0, dtype=float), (length,))
        level = np.broadcast_to(np.asarray(level_db, dtype=float), (length,))
        return start, self.harmonic(f0) * 10 ** (level / 20) * self.edges(length)

    # ---- 파일별 ----

    def build(self, name: str) -> np.ndarray:
        """
        signals.json의 파일 하나를 합성함
        입력: name (signals.json files의 파일 이름)
        출력: 표본 배열(−1~1 기준 실수)
        알고리즘
        1. same_as가 있으면 그 파일의 낭독을 gain_db(dB)만큼 키우고 배경 잡음을 더함(잡음은 그대로)
        2. eval_sentence면 낭독에 배경 잡음을 더함
        3. 그 밖에는 duration_s 길이의 배경 잡음을 만들고, noise_floor면 그대로 돌려줌
        4. 나머지는 parts가 만든 소리들을 각 시작 위치에 더함
        """
        spec = self.s["files"][name]
        if "same_as" in spec:
            base = self.s["files"][spec["same_as"]]
            speech, _ = self.speech(base)
            return speech * 10 ** (spec["gain_db"] / 20) + self.background(len(speech))
        if spec["kind"] == "eval_sentence":
            speech, _ = self.speech(spec)
            return speech + self.background(len(speech))
        x = self.background(self.n(spec["duration_s"]))
        if name == "noise_floor":
            return x
        for start, part in self.parts(spec):
            x[start : start + len(part)] += part
        return x

    def parts(self, spec: dict) -> list[tuple[int, np.ndarray]]:
        """
        지속 발성·글라이드·음량 단계 파일의 소리 조각들을 만들어 냄
        입력: spec (signals.json의 파일 하나의 만드는 값)
        출력: (시작 표본 번호, 소리 표본 배열) 목록
        알고리즘
        1. steps가 있으면 단계마다 같은 기본주파수와 그 단계의 크기(dBFS)로 소리를 만듦
        2. f0_start_hz가 있으면 로그 척도로 일정하게 오르는 글라이드 하나를 만듦
           - F(t)(기본주파수, Hz) = F_start(시작 기본주파수, Hz) × (F_end(끝 기본주파수, Hz) ÷ F_start)^(t(구간 시작부터의 시간, 초) ÷ T(구간 길이, 초))
        3. 그 밖에는 segments마다 소리를 만듦. level이 decay면 크기가 시간에 따라 줄어듦
           - L(t)(크기, dBFS) = L_0(decay.level_dbfs, dBFS) + b(decay.db_per_s, dB/초) × (t(녹음 시작부터의 시각, 초) − t_0(decay.start_s, 초))
           - 아니면 그 조각의 level_dbfs로 고정함
        """
        if "steps" in spec:
            return [self.tone(st["start_s"], st["end_s"], spec["f0_hz"], st["level_dbfs"]) for st in spec["steps"]]
        if "f0_start_hz" in spec:
            t = np.arange(self.n(spec["end_s"]) - self.n(spec["start_s"])) / self.rate
            ratio = spec["f0_end_hz"] / spec["f0_start_hz"]
            f0 = spec["f0_start_hz"] * ratio ** (t / (spec["end_s"] - spec["start_s"]))
            return [self.tone(spec["start_s"], spec["end_s"], f0, spec["level_dbfs"])]
        parts = []
        for seg in spec["segments"]:
            if seg.get("level") == "decay":
                decay = spec["decay"]
                t = np.arange(self.n(seg["end_s"]) - self.n(seg["start_s"])) / self.rate + seg["start_s"]
                level = decay["level_dbfs"] + decay["db_per_s"] * (t - decay["start_s"])
            else:
                level = seg["level_dbfs"]
            parts.append(self.tone(seg["start_s"], seg["end_s"], spec["f0_hz"], level))
        return parts

    # ---- 말소리 같은 낭독 ----

    def layout(self, spec: dict) -> list[dict]:
        """
        음절마다 [자음 시작, 모음 시작, 모음 끝](초)과 모음, 구절 번호를 정함. 길이는 signals.json의 고정 값을 돌려 씀
        입력: spec (낭독 파일의 만드는 값: lead_s, phrases)
        출력: 음절 목록(start·vowel_start·end 초, vowel 모음 이름, phrase 구절 번호)
        알고리즘
        1. 시각을 lead_s(초)에서 시작함
        2. 구절마다 pause_before_s(초)만큼 시각을 넘김
        3. 음절마다 자음 길이 burst_s(초)와, vowel_s 목록을 차례로 돌려 쓴 모음 길이(초)로 세 시각을 정하고, 모음도 vowel_order를 차례로 돌려 씀
        4. gap_after_syllable번째 음절 뒤에는 gap_s(초)만큼 끊김을 넣음
        """
        syl = self.s["syllable"]
        out, t, index = [], spec["lead_s"], 0
        for p, phrase in enumerate(spec["phrases"]):
            t += phrase.get("pause_before_s", 0.0)
            for i in range(phrase["syllables"]):
                vowel_s = syl["vowel_s"][index % len(syl["vowel_s"])]
                vowel = syl["vowel_order"][index % len(syl["vowel_order"])]
                out.append({"start": t, "vowel_start": t + syl["burst_s"], "end": t + syl["burst_s"] + vowel_s, "vowel": vowel, "phrase": p})
                t = out[-1]["end"]
                if phrase.get("gap_after_syllable") == i + 1:
                    t += phrase["gap_s"]
                index += 1
        return out

    def phrase_f0(self, spec: dict, syllables: list[dict], times: np.ndarray) -> np.ndarray:
        """
        구절마다 시작에서 끝까지 직선으로 바뀌는 기본주파수를 만들어 냄. signals.json 값은 모두 내려감
        입력: spec (낭독 파일의 만드는 값), syllables (layout의 음절 목록), times (기본주파수를 구할 시각 배열, 초)
        출력: 시각마다의 기본주파수 배열(Hz). 구절 밖은 NaN
        알고리즘
        1. NaN으로 채운 배열을 만듦
        2. 구절마다 첫 음절 시작부터 마지막 음절 끝까지의 시각에 f0_start_hz에서 f0_end_hz까지 직선으로 값을 넣음
        """
        f0 = np.full(len(times), np.nan)
        for p, phrase in enumerate(spec["phrases"]):
            members = [s for s in syllables if s["phrase"] == p]
            a, b = members[0]["start"], members[-1]["end"]
            inside = (times >= a) & (times <= b)
            f0[inside] = phrase["f0_start_hz"] + (phrase["f0_end_hz"] - phrase["f0_start_hz"]) * (times[inside] - a) / (b - a)
        return f0

    def speech(self, spec: dict) -> tuple[np.ndarray, list[dict]]:
        """
        말소리 같은 음절로 낭독 하나를 만들어 냄
        입력: spec (낭독 파일의 만드는 값: lead_s, tail_s, level_dbfs, phrases)
        출력: (낭독 표본 배열(배경 잡음 없음), layout의 음절 목록)
        알고리즘
        1. 음절 배치를 정하고, 전체 길이를 마지막 음절 끝 + tail_s(초)로 정함
        2. 표본마다의 기본주파수를 구절별 직선으로 구함
        3. 음절마다 모음 구간에 vowel로 모음 소리(앞 음절 모음에서 넘어옴)를 넣음
        4. 자음 구간에는 2500 Hz~대역 상한 대역 통과 잡음(노이즈 시드 noise_seed + 1)을 그 모음 최대 진폭의 0.3배 크기로 넣음
        5. 모음 부분의 RMS(제곱평균제곱근 진폭)가 10^(level_dbfs ÷ 20)이 되도록 전체 크기를 맞춤
        """
        syl = self.s["syllable"]
        syllables = self.layout(spec)
        length = self.n(syllables[-1]["end"] + spec["tail_s"])
        x = np.zeros(length)
        times = np.arange(length) / self.rate
        f0 = self.phrase_f0(spec, syllables, times)
        rng = np.random.default_rng(self.s["noise_seed"] + 1)
        burst_sos = butter(4, [2500, self.band], "bandpass", fs=self.rate, output="sos")
        vowel_mask = np.zeros(length, dtype=bool)
        previous = syllables[0]["vowel"]
        for s in syllables:
            v0, v1 = self.n(s["vowel_start"]), self.n(s["end"])
            vowel_mask[v0:v1] = True
            x[v0:v1] = self.vowel(f0[v0:v1], previous, s["vowel"])
            b0 = self.n(s["start"])
            burst = sosfiltfilt(burst_sos, rng.standard_normal(v0 - b0 + 200))[100 : 100 + v0 - b0]
            x[b0:v0] = 0.3 * burst / np.max(np.abs(burst)) * np.max(np.abs(x[v0:v1]))
            previous = s["vowel"]
        # 모음 부분의 RMS를 정해진 크기에 맞춘다.
        x *= 10 ** (spec["level_dbfs"] / 20) / np.sqrt(np.mean(x[vowel_mask] ** 2))
        return x, syllables

    def vowel(self, f0: np.ndarray, previous: str, vowel: str) -> np.ndarray:
        """
        성문 펄스와 움직이는 포먼트 3개로 모음 소리 하나를 만들어 냄
        입력: `f0` (표본마다의 기본주파수, Hz), previous (앞 음절의 모음 이름), vowel (이 음절의 모음 이름)
        출력: 모음 표본 배열(크기는 speech가 나중에 맞춤)
        알고리즘
        1. 기본주파수 누적 합으로 주기마다 펄스 하나를 놓고, 1 ÷ (1 − 0.95 z⁻¹) 필터로 성문 기울기를 줌
        2. 포먼트 3개(대역폭 90·110·170 Hz)를 차례로 거침. 중심 주파수는 formant_transition_s(0.06초) 동안 앞 모음 값에서 이 모음 값으로 직선으로 옮겨 가고, 10 ms마다 바꿈
           - 공진기: r = e^(−π × W(대역폭, Hz) ÷ fs(샘플레이트, Hz)), θ = 2π × F(중심 주파수, Hz) ÷ fs, 분자 [1 − r], 분모 [1, −2r cos θ, r²]
        3. 앞 attack_s(0.02초) 동안 직선으로 올라가고 뒤 release_s(0.04초) 동안 직선으로 내려가는 포락선을 곱함
        """
        syl = self.s["syllable"]
        length = len(f0)
        phase = np.cumsum(f0) / self.rate
        pulses = np.diff(np.floor(phase), prepend=np.floor(phase[0]))
        source = lfilter([1], [1, -0.95], pulses)  # 성문 기울기
        t = np.arange(length) / self.rate
        transition = np.clip(t / syl["formant_transition_s"], 0, 1)
        y = source
        block = self.n(0.01)
        for k, bandwidth in enumerate(syl["bandwidths_hz"]):
            a, b = syl["vowels"][previous][k], syl["vowels"][vowel][k]
            out, state = np.zeros(length), np.zeros(2)
            r = np.exp(-np.pi * bandwidth / self.rate)
            for i in range(0, length, block):
                freq = a + (b - a) * transition[i]
                theta = 2 * np.pi * freq / self.rate
                out[i : i + block], state = lfilter([1 - r], [1, -2 * r * np.cos(theta), r * r], y[i : i + block], zi=state)
            y = out
        attack, release = self.n(syl["attack_s"]), self.n(syl["release_s"])
        envelope = np.minimum(1, np.arange(length) / attack) * np.minimum(1, (length - np.arange(length)) / release)
        return y * envelope


# ---- 정답값 ----


def answer(value: float, tolerance: float) -> dict:
    """
    정답값 하나를 값과 허용 범위로 묶음
    입력: value (정답 값, 지표 단위), tolerance (허용 범위 ±, 지표 단위)
    출력: {"value": 소수 넷째 자리까지 반올림한 값, "tolerance": 허용 범위}
    알고리즘
    1. 값을 실수로 바꿔 반올림하고 허용 범위와 함께 돌려줌
    """
    return {"value": round(float(value), 4), "tolerance": tolerance}


def f0_truth(synth: Synth, spec: dict) -> dict:
    """
    모음 구간에서 10 ms마다 잰 기본주파수로 억양 지표의 정답을 구함
    입력: synth (합성기), spec (낭독 파일의 만드는 값)
    출력: {"f0_median": 기본주파수 중앙값(Hz), "f0_sd_semitones": 반음 표준편차(반음), "pitch_range_semitones": 음도 범위(반음)}
    알고리즘
    1. 음절마다 모음 시작 + 5 ms부터 모음 끝 전까지 10 ms 간격(F0_STEP_S, 서버 추출 간격과 같음)으로 시각을 잡음
    2. 그 시각의 구절별 직선 기본주파수를 구하고 중앙값을 냄
    3. st(반음 값) = 12 × log₂(F(기본주파수, Hz) ÷ F_med(중앙값, Hz))로 바꿈
    4. 반음 값의 표준편차(모집단 기준)와, 95백분위 − 5백분위를 돌려줌
    """
    syllables = synth.layout(spec)
    times = np.concatenate(
        [np.arange(s["vowel_start"] + F0_STEP_S / 2, s["end"], F0_STEP_S) for s in syllables]
    )
    hz = synth.phrase_f0(spec, syllables, times)
    median = float(np.median(hz))
    st = 12 * np.log2(hz / median)
    low, high = np.percentile(st, [5, 95])
    return {"f0_median": median, "f0_sd_semitones": float(np.std(st)), "pitch_range_semitones": float(high - low)}


def reading_answers(synth: Synth, spec: dict) -> dict:
    """
    낭독 파일 하나의 말속도·억양·검출 음절 수 정답을 구함
    입력: synth (합성기), spec (낭독 파일의 만드는 값)
    출력: total_s, pause_s, articulation_rate, speech_rate, pause_ratio, f0_median, f0_sd_semitones, pitch_range_semitones, detected_syllables의 정답(answer 형식) 사전
    알고리즘
    1. T_total(전체 낭독 시간, 초) = 마지막 모음 끝 − 첫 자음 시작, T_pause(휴지 시간 합, 초) = 구절 앞 휴지(pause_before_s)의 합, N(음절 수)을 구함
    2. 발화 시작·끝은 발화 검출의 반응 때문에 몇십 ms 어긋날 수 있어 s(여유, 0.08초)를 허용 범위로 둠
    3. 시간 정답: T_total ± 0.08초, T_pause ± 0.05초
    4. AR(조음속도, 음절/초) = N ÷ (T_total − T_pause). 허용 범위 = N ÷ (T_total − T_pause − s) − N ÷ (T_total − T_pause)
    5. SR(전체 말속도, 음절/초) = N ÷ T_total. 허용 범위 = N ÷ (T_total − s) − N ÷ T_total
    6. P(무음 비율, %) = T_pause ÷ T_total × 100, 허용 범위 ± 2%
    7. 억양 정답은 f0_truth로 구함: 기본주파수 중앙값 ± 3%, 반음 표준편차 ± 0.4반음, 음도 범위 ± 1.0반음
    8. 검출 음절 수(보조)는 "읽기 오류 가능" 표시(30% 이상 차이)에만 쓰므로, 정답 N, 허용 범위는 N × 0.3의 정수 부분임
    """
    syllables = synth.layout(spec)
    total = syllables[-1]["end"] - syllables[0]["start"]
    pause = sum(p.get("pause_before_s", 0.0) for p in spec["phrases"])
    count = len(syllables)
    # 발화 시작·끝은 발화 검출의 반응 때문에 몇십 ms 어긋날 수 있다. 그만큼을 허용 범위로 둔다.
    slack = 0.08
    truth = f0_truth(synth, spec)
    return {
        "total_s": answer(total, slack),
        "pause_s": answer(pause, 0.05),
        "articulation_rate": answer(count / (total - pause), round(count / (total - pause - slack) - count / (total - pause), 3)),
        "speech_rate": answer(count / total, round(count / (total - slack) - count / total, 3)),
        "pause_ratio": answer(pause / total * 100, 2.0),
        "f0_median": answer(truth["f0_median"], round(truth["f0_median"] * 0.03, 2)),
        "f0_sd_semitones": answer(truth["f0_sd_semitones"], 0.4),
        "pitch_range_semitones": answer(truth["pitch_range_semitones"], 1.0),
        # 검출 음절 수(보조)는 "읽기 오류 가능" 표시(30% 이상 차이)에만 쓴다. 그래서 허용 범위도 그 기준이다.
        "detected_syllables": answer(count, int(count * 0.3)),
    }


def compute_answers(signals: dict) -> dict:
    """
    signals.json의 만드는 값에서 파일별·한 회차 서버 지표 정답을 식으로 바로 구함
    입력: signals (signals.json 내용)
    출력: answers.json 내용 사전(description, files(파일별 정답), session(recordings, answers))
    알고리즘
    1. noise_floor: 잡음 바닥 = background_noise_dbfs ± 0.5 dB. level_steps: 단계마다 크기(dBFS) ± 0.3 dB
    2. broken_phonation: 가장 긴 발성은 0.15초 끊김을 이어 붙인 첫 두 조각(0.5~5.5초)
       - 최대발성지속시간 = 둘째 조각 끝 − 첫 조각 시작 ± 0.05초, 음량 감쇠 기울기 = db_per_s ± 0.15 dB/초
       - "아" 음량 = 발성 프레임 시간(끊김 제외)의 한가운데 시각의 크기 ± 0.3 dB. 크기가 고르게 줄어서 중앙값이 한가운데 값임
    3. steady_phonation: 최대발성지속시간 = 조각 길이 ± 0.05초, "아" 음량 = level_dbfs ± 0.3 dB, 음량 감쇠 기울기 0 ± 0.15 dB/초
    4. f0_glide: r(끝 ÷ 시작 기본주파수 비), D(반음 폭) = 12 × log₂ r. 로그 척도로 일정하게 오르므로 반음 값은 균등 분포임
       - 캘리브레이션 최저·최고 = F_start(시작 기본주파수, Hz) × r^0.05, × r^0.95 (± 2%)
       - 기본주파수 중앙값 = F_start × r^0.5 ± 1.5%
       - 반음 표준편차 = D ÷ √12 ± 0.2반음, 음도 범위 = D × 0.9 ± 0.5반음
    5. syllable_train, paused_speech: reading_answers로 구함
    6. paused_speech_louder: paused_speech 대비 평균 음량 변화 = gain_db ± 0.2 dB
    7. 한 회차 정답(session): 잡음 바닥, broken_phonation의 최대발성지속시간·"아" 음량·음량 감쇠 기울기, 평균 음량 변화 0 ± 0.01 dB(기준선 회차라서), 캘리브레이션 최저·최고, paused_speech의 말속도·억양 지표 6개
    8. 설명, 파일별 정답, 한 회차(signals.json의 session.recordings와 정답)를 묶어 돌려줌
    """
    synth = Synth(signals)
    files = signals["files"]
    out: dict = {}

    out["noise_floor"] = {"noise_floor": answer(signals["background_noise_dbfs"], 0.5)}
    out["level_steps"] = {"step_levels_dbfs": [answer(st["level_dbfs"], 0.3) for st in files["level_steps"]["steps"]]}

    broken = files["broken_phonation"]
    first, second = broken["segments"][0], broken["segments"][1]
    decay = broken["decay"]
    # 가장 긴 발성은 0.15초 끊김을 이어 붙인 0.5~5.5초다. 음량 중앙값은 발성 프레임 시간의 한가운데 값이다.
    voiced = (first["end_s"] - first["start_s"]) + (second["end_s"] - second["start_s"])
    middle = first["start_s"] + voiced / 2 if voiced / 2 <= first["end_s"] - first["start_s"] else second["start_s"] + voiced / 2 - (first["end_s"] - first["start_s"])
    out["broken_phonation"] = {
        "max_phonation_time": answer(second["end_s"] - first["start_s"], 0.05),
        "loudness_decay_slope": answer(decay["db_per_s"], 0.15),
        "sustained_level": answer(decay["level_dbfs"] + decay["db_per_s"] * (middle - decay["start_s"]), 0.3),
    }
    steady = files["steady_phonation"]["segments"][0]
    out["steady_phonation"] = {
        "max_phonation_time": answer(steady["end_s"] - steady["start_s"], 0.05),
        "sustained_level": answer(steady["level_dbfs"], 0.3),
        "loudness_decay_slope": answer(0.0, 0.15),
    }

    glide = files["f0_glide"]
    ratio = glide["f0_end_hz"] / glide["f0_start_hz"]
    span = 12 * np.log2(ratio)  # 반음 폭. 로그 척도로 일정하게 오르므로 반음 값은 균등 분포다.
    out["f0_glide"] = {
        "calibration_low_hz": answer(glide["f0_start_hz"] * ratio**0.05, round(glide["f0_start_hz"] * ratio**0.05 * 0.02, 2)),
        "calibration_high_hz": answer(glide["f0_start_hz"] * ratio**0.95, round(glide["f0_start_hz"] * ratio**0.95 * 0.02, 2)),
        "f0_median": answer(glide["f0_start_hz"] * ratio**0.5, round(glide["f0_start_hz"] * ratio**0.5 * 0.015, 2)),
        "f0_sd_semitones": answer(span / np.sqrt(12), 0.2),
        "pitch_range_semitones": answer(span * 0.9, 0.5),
    }

    out["syllable_train"] = reading_answers(synth, files["syllable_train"])
    out["paused_speech"] = reading_answers(synth, files["paused_speech"])
    out["paused_speech_louder"] = {
        "mean_level_change_from_paused_speech": answer(files["paused_speech_louder"]["gain_db"], 0.2),
    }

    # 한 회차로 보낼 때의 서버 지표 정답. 평균 음량 변화는 기준선 회차(첫 회차)라 0이다.
    session_answers = {
        "noise_floor": out["noise_floor"]["noise_floor"],
        "max_phonation_time": out["broken_phonation"]["max_phonation_time"],
        "sustained_level": out["broken_phonation"]["sustained_level"],
        "loudness_decay_slope": out["broken_phonation"]["loudness_decay_slope"],
        "mean_level_change": answer(0.0, 0.01),
        "calibration_low_hz": out["f0_glide"]["calibration_low_hz"],
        "calibration_high_hz": out["f0_glide"]["calibration_high_hz"],
    }
    for name in ("articulation_rate", "speech_rate", "pause_ratio", "f0_median", "f0_sd_semitones", "pitch_range_semitones"):
        session_answers[name] = out["paused_speech"][name]
    return {
        "description": "합성 신호의 정답값. generate.py --answers로 만든다. 값마다 허용 범위(tolerance, ±)가 있다.",
        "files": out,
        "session": {"recordings": signals["session"]["recordings"], "answers": session_answers},
    }


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    """
    표본 배열을 모노 16비트 PCM WAV 파일로 씀
    입력: path (쓸 파일 경로), samples (표본 배열, −1~1 기준), rate (샘플레이트, Hz)
    출력: 없음. path에 WAV 파일을 씀
    알고리즘
    1. −1~1로 자르고 32767을 곱해 반올림한 16비트 정수(리틀 엔디언)로 바꿈
    2. 모노·2바이트 표본·rate로 WAV를 씀
    """
    pcm = np.round(np.clip(samples, -1, 1) * FULL_SCALE).astype("<i2")
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm.tobytes())


def render_answers(signals: dict) -> str:
    """
    정답값 사전을 answers.json 문자열로 바꿈
    입력: signals (signals.json 내용)
    출력: 들여쓰기 2칸, 한글을 그대로 둔 JSON 문자열. 끝에 줄바꿈 하나
    알고리즘
    1. compute_answers 결과를 JSON으로 바꾸고 끝에 줄바꿈을 붙임
    """
    return json.dumps(compute_answers(signals), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """
    합성 신호 WAV를 만들거나 answers.json을 다시 쓰는 명령을 실행함
    입력: argv (명령줄 인자 목록, 없으면 sys.argv. --out: WAV를 만들 폴더, 기본 이 스크립트가 있는 폴더. --answers: WAV 대신 answers.json을 다시 씀), 이 폴더의 signals.json
    출력: 종료 코드 0. --answers면 answers.json을 씀(줄바꿈 \\n 고정), 아니면 --out 폴더에 파일마다 {이름}.wav를 씀. 쓴 파일을 표준 출력에 알림
    알고리즘
    1. 명령줄 인자와 signals.json을 읽음
    2. --answers면 answers.json을 다시 쓰고 0을 돌려줌
    3. 아니면 폴더를 만들고, signals.json의 파일마다 합성해 WAV로 쓰고 경로와 설명을 출력함
    """
    parser = argparse.ArgumentParser(description="합성 신호(WAV)와 정답값을 만든다")
    parser.add_argument("--out", type=Path, default=HERE, help="WAV를 만들 폴더(기본: 이 스크립트가 있는 폴더)")
    parser.add_argument("--answers", action="store_true", help="WAV 대신 answers.json을 다시 쓴다")
    args = parser.parse_args(argv)

    signals = load_signals()
    if args.answers:
        ANSWERS.write_text(render_answers(signals), encoding="utf-8", newline="\n")
        print(f"{ANSWERS}을 썼습니다")
        return 0
    synth = Synth(signals)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, spec in signals["files"].items():
        path = args.out / f"{name}.wav"
        write_wav(path, synth.build(name), synth.rate)
        print(f"{path}: {spec['description']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
