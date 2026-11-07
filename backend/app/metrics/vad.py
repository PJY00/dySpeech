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
        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])

    def probabilities(self, samples: np.ndarray) -> np.ndarray:
        """덩어리마다 발화 확률. 마지막 덩어리는 0으로 채워 길이를 맞춘다."""
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
    return SpeechDetector()
