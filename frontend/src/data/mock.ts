// 임상가 화면(데모)용 임시 데이터. 임상가 화면은 서버 역할·인증이 정해지기 전까지 데모로 둔다.

export const patient = {
  name: '홍길동',
  sex: 'M',
  age: 68,
  diagnosis: '뇌졸중 후 마비말장애',
  startedAt: '2026.10.30',
};

export type SessionRecord = {
  session: number;
  date: string;
  db: number;
  rate: number;
  pauseRatio: number;
};

export const sessionRecords: SessionRecord[] = [
  { session: 1, date: '2026.10.30', db: -31.4, rate: 3.42, pauseRatio: 24 },
  { session: 2, date: '2026.10.31', db: -30.8, rate: 3.36, pauseRatio: 22 },
  { session: 3, date: '2026.11.01', db: -30.2, rate: 3.28, pauseRatio: 20 },
  { session: 4, date: '2026.11.02', db: -29.8, rate: 3.21, pauseRatio: 19 },
  { session: 5, date: '2026.11.03', db: -29.5, rate: 3.18, pauseRatio: 19 },
  { session: 6, date: '2026.11.04', db: -31.7, rate: 3.08, pauseRatio: 21 },
  { session: 7, date: '2026.11.05', db: -28.9, rate: 3.12, pauseRatio: 18 },
  { session: 8, date: '2026.11.06', db: -28.3, rate: 3.05, pauseRatio: 16 },
];

// 기준 회차 대비 변화율(%)
