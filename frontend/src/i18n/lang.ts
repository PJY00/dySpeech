// 화면 언어(한국어/영어). 설정 탭에서 바꾸고, 문구는 L(한국어, 영어)로 고른다.
// React와 네이티브 모듈을 쓰지 않는 순수 모듈이라 게임 로직·측정 검사 코드와 테스트에서도 쓸 수 있다.
// 사용자가 읽고 점수를 내는 한국어 문장(평가 문장, 속도 맞춰 읽기 문장)은 언어와 상관없이 그대로 둔다.

export type Lang = 'ko' | 'en';

let current: Lang = 'ko';
const listeners = new Set<() => void>();

export function getLang(): Lang {
  return current;
}

export function setLangValue(lang: Lang): void {
  if (lang === current) return;
  current = lang;
  for (const listener of listeners) listener();
}

export function subscribeLang(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 지금 언어에 맞는 문구 */
export function L(ko: string, en: string): string {
  return current === 'en' ? en : ko;
}

const MONTHS_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** 회차 시각 표시. 한국어 "11월 12일 10시 5분", 영어 "Nov 12, 10:05" */
export function formatWhen(d: Date): string {
  if (current === 'en') {
    return `${MONTHS_EN[d.getMonth()]} ${d.getDate()}, ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }
  return `${d.getMonth() + 1}월 ${d.getDate()}일 ${d.getHours()}시 ${d.getMinutes()}분`;
}
