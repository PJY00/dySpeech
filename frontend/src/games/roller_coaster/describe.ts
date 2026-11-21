// 라운드 끝 화면에 보여 줄 판정 한 줄(롤러코스터). 채점이 우주선과 같아 같은 문구를 쓰고, 모으는 것만 코인이다.
import type { GameRun } from '../../session/types';
import { describeLaneRun } from '../spaceship/describe';

export function describeRollerCoasterRun(run: GameRun): string | null {
  return describeLaneRun(run, ['코인', 'coins']);
}
