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
    return json.loads(path.read_text(encoding="utf-8"))


class Synth:
    def __init__(self, signals: dict) -> None:
        self.s = signals
        self.rate = signals["format"]["sample_rate"]
        self.band = signals["band_limit_hz"]
        self.ramp = round(signals["ramp_ms"] / 1000 * self.rate)

    # ---- 재료 ----

    def n(self, seconds: float) -> int:
        return round(seconds * self.rate)

    def noise(self, length: int, level_dbfs: float, seed_offset: int = 0) -> np.ndarray:
        """100 Hz~대역 상한의 잡음. 16 kHz로 바꿔도 크기가 그대로이도록 대역을 묶는다."""
        rng = np.random.default_rng(self.s["noise_seed"] + seed_offset)
        sos = butter(4, [100, self.band], "bandpass", fs=self.rate, output="sos")
        x = sosfiltfilt(sos, rng.standard_normal(length + 2 * self.rate))[self.rate : self.rate + length]
        return x * 10 ** (level_dbfs / 20) / np.sqrt(np.mean(x * x))

    def background(self, length: int) -> np.ndarray:
        return self.noise(length, self.s["background_noise_dbfs"])

    def harmonic(self, f0: np.ndarray) -> np.ndarray:
        """배음 1/k 진폭의 소리. RMS가 1이 되도록 맞춘다(배음 수가 일정하므로 식으로 정해진다)."""
        count = int(self.band // np.max(f0))
        phase = 2 * np.pi * np.cumsum(f0) / self.rate
        y = sum(np.sin(k * phase) / k for k in range(1, count + 1))
        return y / np.sqrt(sum(1 / k**2 for k in range(1, count + 1)) / 2)

    def edges(self, length: int) -> np.ndarray:
        """앞뒤 짧은 상승·하강(딸깍 소리 방지)"""
        gain = np.ones(length)
        ramp = min(self.ramp, length // 2)
        if ramp:
            curve = 0.5 - 0.5 * np.cos(np.pi * np.arange(ramp) / ramp)
            gain[:ramp] = curve
            gain[length - ramp :] = curve[::-1]
        return gain

    def tone(self, start_s: float, end_s: float, f0: np.ndarray | float, level_db: np.ndarray | float) -> tuple[int, np.ndarray]:
        start, length = self.n(start_s), self.n(end_s) - self.n(start_s)
        f0 = np.broadcast_to(np.asarray(f0, dtype=float), (length,))
        level = np.broadcast_to(np.asarray(level_db, dtype=float), (length,))
        return start, self.harmonic(f0) * 10 ** (level / 20) * self.edges(length)

    # ---- 파일별 ----

    def build(self, name: str) -> np.ndarray:
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
        """음절마다 [자음 시작, 모음 시작, 모음 끝](초)과 모음, 구절 번호. 길이는 signals.json의 고정 값을 돌려 쓴다."""
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
        """구절마다 시작에서 끝까지 직선으로 내려가는 기본주파수"""
        f0 = np.full(len(times), np.nan)
        for p, phrase in enumerate(spec["phrases"]):
            members = [s for s in syllables if s["phrase"] == p]
            a, b = members[0]["start"], members[-1]["end"]
            inside = (times >= a) & (times <= b)
            f0[inside] = phrase["f0_start_hz"] + (phrase["f0_end_hz"] - phrase["f0_start_hz"]) * (times[inside] - a) / (b - a)
        return f0

    def speech(self, spec: dict) -> tuple[np.ndarray, list[dict]]:
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
    return {"value": round(float(value), 4), "tolerance": tolerance}


def f0_truth(synth: Synth, spec: dict) -> dict:
    """모음 구간에서 10 ms마다 잰 기본주파수로 억양 지표의 정답을 구한다."""
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
    pcm = np.round(np.clip(samples, -1, 1) * FULL_SCALE).astype("<i2")
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm.tobytes())


def render_answers(signals: dict) -> str:
    return json.dumps(compute_answers(signals), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
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
