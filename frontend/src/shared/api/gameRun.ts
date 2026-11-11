export type Decision = 'up' | 'down' | 'hold' | 'deferred';

export interface GameRun {
  /** 게임 한 판(라운드)의 ID. UUID v4 */
  game_run_id: string;
  /** 게임 이름. 영문 소문자 snake_case, 64자 이하 */
  game: string;
  level: number;
  round_index: number;
  /** 시간대가 있는 ISO 8601 시각 */
  started_at: string;
  params?: object;
  /** 성공률(%). 0~100 */
  success_rate_pct: number;
  decision: Decision;
  decision_reason?: string;
  result?: object;
}

export function uuidv4(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.floor(Math.random() * 16);
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
