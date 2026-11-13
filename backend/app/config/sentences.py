from dataclasses import dataclass


@dataclass(frozen=True)
class EvalSentence:
    sentence_id: str
    text: str
    # 한글 음절 글자 수. 말속도 계산의 음절 수로 쓴다.
    syllables: int


# 평가 전용 문장. 매 회차 같은 문장을 읽는다.
EVAL_SENTENCES: dict[str, EvalSentence] = {
    "s1": EvalSentence("s1", "우리는 저녁마다 노래를 부르며 마을을 걸었습니다", 21),
}
