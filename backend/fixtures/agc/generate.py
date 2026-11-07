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
    """
    자동 게인 조절 시험 소리의 정답값(signals.json)을 읽음
    입력: path (설정 파일 경로, 기본 이 폴더의 signals.json)
    출력: 정답값 사전
    알고리즘
    1. 파일을 UTF-8로 읽어 JSON으로 바꿔 돌려줌
    """
    return json.loads(path.read_text(encoding="utf-8"))


def synthesize(signals: dict, name: str) -> array:
    """
    시험 소리 하나의 16비트 표본 배열을 만들어 냄. 구간마다 앞뒤에 짧게 소리를 키우고 줄여 딸깍 소리를 막음
    입력: signals (signals.json 내용: 샘플레이트, frequency_hz 정현파 주파수(Hz), ramp_ms 상승·하강 길이(밀리초), 파일별 구간), name (파일 이름: agc_steps·agc_hold·agc_toggle)
    출력: 16비트 정수 표본 배열(길이 duration_s × 샘플레이트)
    알고리즘
    1. duration_s(초) 길이의 0(무음) 배열을 만듦
    2. 구간마다 정현파 진폭 A = √2 × 10^(L(level_dbfs, dBFS) ÷ 20)로 정함(정현파는 진폭 = 제곱평균제곱근 진폭 × √2)
    3. 구간 안 표본마다 sin(2π × f(frequency_hz, Hz) × i(구간 시작부터의 표본 번호) ÷ fs(샘플레이트, Hz))에 A와 32767을 곱해 반올림함. 위상은 구간마다 0에서 시작함
    4. 구간 앞뒤 ramp_ms 안에서는 0.5 − 0.5 × cos(π × e ÷ R)(e는 가까운 끝까지의 표본 수, R은 상승 표본 수) 이득을 곱함
    """
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
    """
    16비트 표본 배열을 모노 PCM WAV 파일로 씀
    입력: path (쓸 파일 경로), samples (16비트 정수 표본 배열), rate (샘플레이트, Hz)
    출력: 없음. path에 WAV 파일을 씀
    알고리즘
    1. 이 컴퓨터가 빅 엔디언이면 표본을 복사해 바이트 순서를 바꿈(WAV는 리틀 엔디언)
    2. 모노·2바이트 표본·rate로 WAV를 씀
    """
    if sys.byteorder == "big":
        samples = array("h", samples)
        samples.byteswap()
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(samples.tobytes())


def main(argv: list[str] | None = None) -> int:
    """
    자동 게인 조절 시험 소리 WAV 3개를 만드는 명령을 실행함
    입력: argv (명령줄 인자 목록, 없으면 sys.argv. --out: WAV를 만들 폴더, 기본 이 스크립트가 있는 폴더), 이 폴더의 signals.json
    출력: 종료 코드 0. --out 폴더에 파일마다 {이름}.wav를 쓰고, 경로·길이(초)·설명을 표준 출력에 알림
    알고리즘
    1. 명령줄 인자와 signals.json을 읽고 폴더를 만듦
    2. signals.json의 파일마다 표본을 합성해 WAV로 쓰고 출력함
    """
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
