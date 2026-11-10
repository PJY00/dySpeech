// 노래방 따라 읽기 훈련 문장. 평가 문장 s1은 넣지 않는다(s1과 겹치는 낱말도 피함: 우리, 저녁, 노래, 부르며, 마을, 걸었습니다).
// 조건: 평서문, 숫자·외래어 없음. 음절 수는 한글 음절 글자(가~힣) 수이고 말속도 계산의 N(정답 음절 수)으로 쓴다.

export interface TrainingSentence {
  id: string;
  text: string;
  syllables: number;
}

export const TRAINING_SENTENCES: readonly TrainingSentence[] = [
  { id: 'g01', text: '나는 아침마다 물을 마셔요', syllables: 11 },
  { id: 'g02', text: '작은 새가 나무 위에서 울어요', syllables: 12 },
  { id: 'g03', text: '어머니와 함께 시장에 갔어요', syllables: 12 },
  { id: 'g04', text: '바람이 불어서 나뭇잎이 날려요', syllables: 13 },
  { id: 'g05', text: '오늘은 날씨가 맑고 따뜻해요', syllables: 12 },
  { id: 'g06', text: '아이들이 놀이터에서 웃고 있어요', syllables: 14 },
  { id: 'g07', text: '강아지가 마당에서 뛰어놀아요', syllables: 13 },
  { id: 'g08', text: '할머니께 편지를 천천히 읽어 드렸어요', syllables: 16 },
  { id: 'g09', text: '내일은 친구와 공원에 갈 거예요', syllables: 13 },
  { id: 'g10', text: '따뜻한 차를 마시고 책을 읽어요', syllables: 13 },
];

/** 한 라운드의 문장 수 */
export const SENTENCES_PER_ROUND = 5;

/** 한글 음절 글자(U+AC00~U+D7A3) 수 */
export function hangulSyllables(text: string): number {
  let n = 0;
  for (const ch of text) {
    const c = ch.codePointAt(0) ?? 0;
    if (c >= 0xac00 && c <= 0xd7a3) n += 1;
  }
  return n;
}

/** 라운드의 문장 5개. 라운드 번호마다 5개씩 돌아가며 고른다(1라운드 g01~g05, 2라운드 g06~g10, 3라운드 다시 g01~) */
export function roundSentences(roundIndex: number, count = SENTENCES_PER_ROUND): TrainingSentence[] {
  const start = ((Math.max(1, roundIndex) - 1) * count) % TRAINING_SENTENCES.length;
  return Array.from({ length: count }, (_, i) => TRAINING_SENTENCES[(start + i) % TRAINING_SENTENCES.length]);
}
