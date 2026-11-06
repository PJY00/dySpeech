"""자동 게인 조절 시험 소리(WAV)를 signals.json의 정답값대로 만든다.

Python 3.9 이상의 표준 라이브러리만 쓴다(macOS 기본 python3로도 돈다). 어느 폴더에서든 실행할 수 있다.
    python3 backend/fixtures/agc/generate.py              이 스크립트가 있는 폴더에 만든다
    python3 backend/fixtures/agc/generate.py --out 폴더    지정한 폴더에 만든다

WAV는 저장소에 올리지 않는다(.gitignore). 필요할 때 이 스크립트로 만든다.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import wave
from array import array
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIGNALS = HERE / "signals.json"
FULL_SCALE = 32767


def load_signals(path: Path = SIGNALS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def synthesize(signals: dict, name: str) -> array:
    """16비트 표본 배열을 만든다. 구간마다 앞뒤에 짧게 소리를 키우고 줄여 딸깍 소리를 막는다."""
    rate = signals["format"]["sample_rate"]
    frequency = signals["frequency_hz"]
    ramp = round(signals["ramp_ms"] / 1000 * rate)
    spec = signals["files"][name]

    samples = array("h", bytes(2 * round(spec["duration_s"] * rate)))
    for segment in spec["segments"]:
        start = round(segment["start_s"] * rate)
        length = round(segment["end_s"] * rate) - start
        amplitude = math.sqrt(2) * 10 ** (segment["level_dbfs"] / 20)
        for i in range(length):
            edge = min(i, length - 1 - i)
            gain = 0.5 - 0.5 * math.cos(math.pi * edge / ramp) if edge < ramp else 1.0
            samples[start + i] = round(FULL_SCALE * amplitude * gain * math.sin(2 * math.pi * frequency * i / rate))
    return samples


def write_wav(path: Path, samples: array, rate: int) -> None:
    if sys.byteorder == "big":
        samples = array("h", samples)
        samples.byteswap()
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(samples.tobytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="자동 게인 조절 시험 소리(WAV)를 만든다")
    parser.add_argument("--out", type=Path, default=HERE, help="WAV를 만들 폴더(기본: 이 스크립트가 있는 폴더)")
    args = parser.parse_args(argv)

    signals = load_signals()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, spec in signals["files"].items():
        path = args.out / f"{name}.wav"
        write_wav(path, synthesize(signals, name), signals["format"]["sample_rate"])
        print(f"{path} ({spec['duration_s']:g}초): {spec['description']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
