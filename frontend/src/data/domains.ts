// 게임 영역의 화면 표시(이름, 아이콘, 색). id는 API·게임 등록부의 영역 이름(GameArea)을 그대로 쓴다.
// 이름은 지금 언어로 읽히도록 getter로 둔다.
import type { ComponentProps } from 'react';
import type { MaterialCommunityIcons } from '@expo/vector-icons';

import { L } from '@/i18n/lang';
import type { GameArea } from '@/training/gameTypes';
import type { MetricName } from '@/session';

export type DomainId = GameArea;

export type Domain = {
  id: DomainId;
  number: number;
  title: string;
  /** 기록 탭 필터처럼 좁은 곳에 쓰는 짧은 이름 */
  shortTitle: string;
  games: string;
  icon: ComponentProps<typeof MaterialCommunityIcons>['name'];
  color: string;
  tint: string;
  /** 이 영역의 진전도 지표(api.md 7.1). 첫 번째가 대표 지표 */
  metrics: MetricName[];
};

export const domains: Domain[] = [
  {
    id: 'breath',
    number: 1,
    get title() {
      return L('호흡 조절', 'Breath control');
    },
    get shortTitle() {
      return L('호흡', 'Breath');
    },
    get games() {
      return L('풍선 불기 · 촛불 지키기', 'Blow Up the Balloon · Keep the Candle Lit');
    },
    icon: 'balloon',
    color: '#2FAF97',
    tint: '#E7F7F2',
    metrics: ['max_phonation_time', 'loudness_decay_slope', 'mean_level_change'],
  },
  {
    id: 'rate',
    number: 2,
    get title() {
      return L('말 속도', 'Speech rate');
    },
    get shortTitle() {
      return L('말 속도', 'Rate');
    },
    get games() {
      return L('속도 맞춰 읽기', 'Pace Reading');
    },
    icon: 'book-open-variant',
    color: '#4A7CF0',
    tint: '#EAF1FE',
    metrics: ['articulation_rate', 'speech_rate', 'pause_ratio'],
  },
  {
    id: 'intonation',
    number: 3,
    get title() {
      return L('목소리 · 억양', 'Voice · Intonation');
    },
    get shortTitle() {
      return L('억양', 'Intonation');
    },
    get games() {
      return L('우주선 게임 · 롤러코스터 게임', 'Spaceship · Roller Coaster');
    },
    icon: 'rocket-launch',
    color: '#8E5CF0',
    tint: '#F2ECFD',
    metrics: ['pitch_range_semitones', 'f0_sd_semitones'],
  },
];

export function findDomain(id: string | undefined): Domain | undefined {
  return domains.find((domain) => domain.id === id);
}
