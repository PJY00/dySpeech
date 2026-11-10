// 하루 단위 기록에 쓰는 날짜 키 (기기 현지 시간 기준)
export function todayKey(): string {
  const now = new Date();
  return `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
}
