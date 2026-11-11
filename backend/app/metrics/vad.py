"""발화 확률: silero-vad(ONNX)를 onnxruntime으로 돌린다. 16 kHz, 512표본(32 ms)마다 확률 하나."""

from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime

from app.metrics.audio import RATE

MODEL_PATH = Path(__file__).parent / "assets" / "silero_vad.onnx"
# silero-vad v6.2.3 저장소의 src/silero_vad/data/silero_vad.onnx. 바꾸면 algorithm_version을 올린다.
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
CHUNK = 512
CONTEXT = 64  # 원 구현처럼 앞 덩어리의 끝 64표본을 앞에 붙인다
CHUNK_S = CHUNK / RATE


class SpeechDetector:
    def __init__(self, model_path: Path = MODEL_PATH) -> None:
        """
        silero-vad 발화 검출 모델(ONNX)을 CPU에서 돌릴 준비를 함
        입력: model_path (모델 파일 경로, 기본값 app/metrics/assets/silero_vad.onnx)
        출력: 없음 (self.session에 onnxruntime 추론 세션을 둠)
        알고리즘
        1. onnxruntime 세션 설정에서 연산 사이·연산 안 스레드를 각각 1개로 정함
        2. CPUExecutionProvider로 추론 세션을 만들어 둠
        """
        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])

    def probabilities(self, samples: np.ndarray) -> np.ndarray:
        """
        32 ms(512표본) 덩어리마다 발화 확률을 구함
        입력: samples (16 kHz 표본, −1~1)
        출력: 덩어리별 발화 확률 배열(0~1). 길이 = 표본 수 ÷ 512의 올림
        알고리즘
        1. 마지막 덩어리가 512표본이 되도록 뒤를 0으로 채워 길이를 맞춤
        2. 모델 상태(2 × 1 × 128)와 앞 문맥(64표본)을 0으로 시작함
        3. 덩어리마다 앞 덩어리의 끝 64표본을 앞에 붙인 576표본을 넣어(원 구현과 같음) 발화 확률과 새 상태를 받음(sr = 16000 Hz)
        4. 이번 입력의 끝 64표본을 다음 덩어리의 문맥으로 둠
        """
        x = np.pad(np.asarray(samples, dtype=np.float32), (0, (-len(samples)) % CHUNK))
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros(CONTEXT, dtype=np.float32)
        rate = np.array(RATE, dtype=np.int64)
        probabilities = np.empty(len(x) // CHUNK)
        for index in range(len(probabilities)):
            window = np.concatenate([context, x[index * CHUNK : (index + 1) * CHUNK]])[None, :]
            output, state = self.session.run(None, {"input": window, "state": state, "sr": rate})
            context = window[0, -CONTEXT:]
            probabilities[index] = output[0, 0]
        return probabilities


@lru_cache
def get_detector() -> SpeechDetector:
    """
    발화 검출기를 한 번만 만들어 다시 씀
    입력: 없음
    출력: SpeechDetector(기본 모델 파일)
    알고리즘
    1. 처음 부를 때 만들고, 그 뒤에는 lru_cache로 같은 객체를 돌려줌
    """
    return SpeechDetector()
