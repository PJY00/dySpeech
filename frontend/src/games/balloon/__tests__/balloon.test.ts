import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { BalloonMode, BalloonState } from '../balloonLogic';
import {
  DEFAULT_MPT_S,
  diffStats,
  getLevel,
  GRACE_S,
  initialState,
  isRoundOver,
  levelLabel,
  MAX_HOLD_S,
  MAX_LEVEL,
  MIN_HOLD_S,
  MODE_OF_GAME,
  MODES,
  ROUND_SECONDS,
  stepBalloon,
  summarizeRound,
} from '../balloonLogic';
import { FLAME_OUT_BELOW, flameColor, flameKelvin, sceneColor } from '../flameColor';

const T = { deltaDb: 6, holdSeconds: 3 };
const DT = 0.02;

/** 같은 dB 차이로 seconds초 동안 프레임을 넣는다 */
function run(state: BalloonState, diff: number, seconds: number, voiced = true, mode: BalloonMode = 'protect'): BalloonState {
  let s = state;
  for (let i = 0; i < Math.round(seconds / DT); i++) s = stepBalloon(s, diff, DT, T, voiced, mode);
  return s;
}

// ---- 크기가 움직이는 방식 ----

test('게임별 방식: 풍선은 키우기(build), 촛불은 지키기(protect)', () => {
  assert.equal(MODE_OF_GAME.balloon, 'build');
  assert.equal(MODE_OF_GAME.candle, 'protect');
});

test('풍선(build): 0에서 시작하고, 소리가 목표 이상이면 커지며 가득을 넘지 않는다', () => {
  const start = initialState('build');
  assert.equal(start.size, 0);
  const s = run(start, 8, 1, true, 'build');
  assert.ok(s.size > 0 && s.size < 1);
  // 목표 시간이 아주 길어서 중간에 끝나지 않는 경우: 계속 커져도 가득(1)을 넘지 않는다
  let full = start;
  for (let i = 0; i < 400; i++) full = stepBalloon(full, 30, DT, { deltaDb: 6, holdSeconds: 99 }, true, 'build');
  assert.equal(full.size, 1);
});

test('풍선(build): 천천히 커지므로 짧은 목표를 채운 순간에는 아직 가득 차지 않았다(화면이 끝나는 동안 끝까지 부풀려 준다)', () => {
  const done = run(initialState('build'), 8, 3.2, true, 'build');
  assert.equal(done.passed, true);
  assert.ok(done.size < 1);
});

test('풍선(build): 목표에 못 미치면 작아지고 0 아래로 내려가지 않는다', () => {
  const grown = run(initialState('build'), 8, 2.5, true, 'build');
  const shrunk = run(grown, 0, 1.5, true, 'build');
  assert.ok(shrunk.size < grown.size);
  assert.equal(run(initialState('build'), -30, 5, true, 'build').size, 0);
});

test('촛불(protect): 가득(1)에서 시작하고, 목표 이상이면 유지되며 유지 시간이 쌓인다', () => {
  const start = initialState('protect');
  assert.equal(start.size, 1);
  const s = run(start, 8, 1);
  assert.equal(s.size, 1);
  assert.ok(Math.abs(s.holdTime - 1) < 0.05);
  assert.equal(s.passed, false);
});

test('촛불(protect): 첫 소리가 나기 전에는 시작 여유까지 약해지지 않는다', () => {
  const grace = MODES.protect.startGraceS;
  const quiet = run(initialState('protect'), -40, grace - 0.1, false);
  assert.equal(quiet.size, 1);
  const later = run(quiet, -40, 1, false);
  assert.ok(later.size < 1, '여유가 지나면 약해지기 시작한다');
});

test('촛불(protect): 소리를 한 번이라도 내면 목표에 못 미치는 순간부터 약해진다', () => {
  const voiced = run(initialState('protect'), 0, 0.1, true); // 목표(+6)에는 못 미치지만 소리는 남
  assert.ok(run(voiced, 0, 1, true).size < 1, '시작 여유를 기다리지 않는다');
});

test('촛불(protect): 소리가 계속 모자라면 완전히 꺼지고 0에서 멈춘다', () => {
  assert.equal(run(initialState('protect'), 0, ROUND_SECONDS - 1, true).size, 0);
});

test('촛불(protect): 꺼진 뒤 소리가 돌아오면 다시 밝아진다', () => {
  const out = run(initialState('protect'), 0, ROUND_SECONDS - 1, true);
  assert.equal(out.size, 0);
  const lit = run(out, 8, 1, true);
  assert.ok(lit.size > FLAME_OUT_BELOW, '불이 다시 붙는다');
  assert.ok(MODES.protect.riseSpeed > MODES.protect.fallSpeed, '되살아나는 속도가 약해지는 속도보다 빠르다');
});

test('움직임은 너무 빠르지 않다: 가득↔빔이 3초 이상 걸린다', () => {
  for (const mode of ['build', 'protect'] as const) {
    assert.ok(1 / MODES[mode].riseSpeed >= 3, `${mode} 차오르는 데 3초 이상`);
    assert.ok(1 / MODES[mode].fallSpeed >= 3, `${mode} 줄어드는 데 3초 이상`);
  }
});

// ---- 목표를 채우는 판정(두 방식 공통) ----

test('목표 미만이 GRACE_S를 넘으면 유지 시간이 0으로 돌아간다', () => {
  const grown = run(initialState('protect'), 8, 1.5);
  const s = run(grown, 2, 0.6);
  assert.ok(s.size < grown.size);
  assert.equal(s.holdTime, 0);
  assert.ok(s.bestHold >= 1.4, '가장 오래 유지한 시간은 남는다');
});

test('GRACE_S 이하의 짧은 끊김은 유지 시간과 크기를 그대로 둔다', () => {
  for (const mode of ['build', 'protect'] as const) {
    const before = run(initialState(mode), 8, 1, true, mode);
    const dipped = run(before, 0, GRACE_S - 0.05, true, mode);
    assert.equal(dipped.holdTime, before.holdTime);
    assert.equal(dipped.size, before.size);
    const resumed = run(dipped, 8, 1, true, mode);
    assert.ok(Math.abs(resumed.holdTime - 2) < 0.06, '끊긴 시간은 세지 않고 이어서 쌓인다');
  }
});

test('끊김 뒤 목표 이상으로 돌아오면 끊김 시간이 다시 0부터 센다', () => {
  let s = run(initialState('protect'), 8, 1);
  s = run(s, 0, 0.2);
  s = run(s, 8, 0.1);
  s = run(s, 0, 0.2); // 처음 끊김과 합쳐서 0.4초지만 이어진 끊김이 아니다
  assert.ok(s.holdTime > 1);
});

test('목표 시간을 이어서 채우면 목표 달성이고 라운드가 끝난다', () => {
  for (const mode of ['build', 'protect'] as const) {
    const s = run(initialState(mode), 7, 3.2, true, mode);
    assert.equal(s.passed, true);
    assert.equal(isRoundOver(s), true);
    assert.equal(stepBalloon(s, 0, DT, T, true, mode), s, '끝난 뒤에는 상태가 바뀌지 않는다');
  }
});

test('끊김이 길면 유지 시간이 처음부터라 목표를 채우지 못한다', () => {
  let s = run(initialState('protect'), 7, 2);
  s = run(s, 0, 0.6);
  s = run(s, 7, 2);
  assert.equal(s.passed, false);
});

test('목표를 못 채워도 제한 시간(15초)이 지나면 라운드가 끝난다', () => {
  assert.equal(ROUND_SECONDS, 15);
  const s = run(initialState('protect'), 0, ROUND_SECONDS + 1);
  assert.equal(s.passed, false);
  assert.equal(isRoundOver(s), true);
});

// ---- 라운드 결과 ----

test('목표를 채우면 성공률 100%로 다음 단계, 최고 단계는 유지한다', () => {
  const done = run(initialState('protect'), 7, 3.2);
  const up = summarizeRound(done, 1, T);
  assert.equal(up.decision, 'up');
  assert.equal(up.successRatePct, 100);
  assert.equal(summarizeRound(done, MAX_LEVEL, T).decision, 'hold');
});

test('성공률은 목표를 채웠으면 항상 100%다 (앱이 80% 이상을 통과로 본다)', () => {
  // 목표 전에 한참 낮은 소리를 내서 전체 시간 대비 목표 이상 비율은 낮아도 달성도는 100%
  let s = run(initialState('protect'), 0, 4);
  s = run(s, 7, 3.2);
  assert.equal(s.passed, true);
  const sum = summarizeRound(s, 1, T);
  assert.equal(sum.successRatePct, 100);
  assert.ok(sum.aboveTargetPct < 80, '목표 이상이던 시간 비율은 따로 센다');
});

test('못 채우면 같은 단계로 두고 성공률은 목표 대비 가장 오래 유지한 비율이다', () => {
  let s = run(initialState('protect'), 7, 1.5); // 1.5초 유지(목표 3초의 50%)
  s = run(s, 0, ROUND_SECONDS); // 이후 끊김
  assert.equal(s.passed, false);
  const sum = summarizeRound(s, 3, T);
  assert.equal(sum.decision, 'hold');
  assert.ok(Math.abs(sum.successRatePct - 50) < 2);
});

test('단계를 내리지 않는다', () => {
  const failed = run(initialState('protect'), 0, ROUND_SECONDS + 1);
  for (let level = 1; level <= MAX_LEVEL; level++) {
    assert.notEqual(summarizeRound(failed, level, T).decision, 'down');
  }
});

test('소리를 거의 내지 않으면 라운드를 세지 않는다', () => {
  const quiet = run(initialState('protect'), -40, ROUND_SECONDS + 1, false);
  const sum = summarizeRound(quiet, 3, T);
  assert.equal(sum.decision, 'deferred');
  assert.equal(sum.voicedCoveragePct, 0);
});

// ---- 단계 ----

const LEVELS = Array.from({ length: MAX_LEVEL }, (_, i) => i + 1);

test('장기 연습용으로 단계가 10개이고, 범위 밖 단계는 끝값으로 맞춘다', () => {
  assert.equal(MAX_LEVEL, 10);
  assert.equal(getLevel(0).level, 1);
  assert.equal(getLevel(99).level, MAX_LEVEL);
});

test('목표 음량은 두 단계에 1dB씩만 오른다(+6 ~ +10)', () => {
  assert.deepEqual(LEVELS.map((lv) => getLevel(lv).deltaDb), [6, 6, 7, 7, 8, 8, 9, 9, 10, 10]);
});

test('유지 시간: 최대발성지속시간 10초(모를 때)면 1.5초에서 5초까지 0.4초쯤씩 늘어난다', () => {
  assert.equal(DEFAULT_MPT_S, 10);
  assert.deepEqual(
    LEVELS.map((lv) => getLevel(lv, null).holdSeconds),
    [1.5, 1.9, 2.3, 2.7, 3.1, 3.4, 3.8, 4.2, 4.6, 5],
  );
  assert.deepEqual(LEVELS.map((lv) => getLevel(lv, 10).holdSeconds), LEVELS.map((lv) => getLevel(lv, null).holdSeconds));
});

test('유지 시간은 어떤 최대발성지속시간에서도 단계마다 늘어난다(0.1초 이상씩)', () => {
  for (const mpt of [1, 2, 3, 4, 6, 8, 10, 15, 20, 40, 120]) {
    const holds = LEVELS.map((lv) => getLevel(lv, mpt).holdSeconds);
    for (let i = 1; i < holds.length; i++) {
      assert.ok(holds[i] - holds[i - 1] >= 0.1 - 1e-9, `MPT ${mpt}초: ${holds.join(', ')}`);
    }
    assert.ok(holds[0] >= MIN_HOLD_S, '가장 쉬운 단계도 하한 이상');
    assert.ok(holds[holds.length - 1] <= MAX_HOLD_S, '가장 어려운 단계도 상한 이하');
    assert.ok(holds[holds.length - 1] - holds[0] >= 1.5 - 1e-9, '첫 단계와 마지막 단계가 1.5초 이상 벌어진다');
  }
});

test('유지 시간 상한은 장기 연습을 위해 여유 있게 8초다', () => {
  assert.equal(MAX_HOLD_S, 8);
  assert.equal(getLevel(MAX_LEVEL, 120).holdSeconds, 8);
});

test('유지 시간은 최대발성지속시간이 길수록 같거나 길다', () => {
  for (const lv of LEVELS) {
    let prev = 0;
    for (const mpt of [1, 3, 5, 8, 10, 15, 20, 40]) {
      const hold = getLevel(lv, mpt).holdSeconds;
      assert.ok(hold >= prev - 1e-9, `단계 ${lv}, MPT ${mpt}초`);
      prev = hold;
    }
  }
  assert.equal(getLevel(1, 0).holdSeconds, getLevel(1, null).holdSeconds, '0 이하는 모르는 값으로 본다');
});

test('단계 라벨: 이 단계의 목표와 이전 단계와 달라진 점을 보여 준다', () => {
  const first = levelLabel(1, 10);
  assert.equal(first.goal, '기준 +6dB · 1.5초 유지');
  assert.equal(first.change, null, '1단계는 비교할 이전 단계가 없다');

  // 2단계: 시간만 늘고 음량은 같다
  assert.equal(levelLabel(2, 10).goal, '기준 +6dB · 1.9초 유지');
  assert.equal(levelLabel(2, 10).change, '지난 단계보다 유지 시간 +0.4초');
  // 3단계: 시간과 음량이 같이 오른다
  assert.equal(levelLabel(3, 10).change, '지난 단계보다 유지 시간 +0.4초, 음량 +1dB');

  // 어느 단계든 이전보다 시간이 늘어난다고 알려 준다
  for (const mpt of [3, 6, 10, 20]) {
    for (const lv of LEVELS.slice(1)) {
      assert.match(levelLabel(lv, mpt).change ?? '', /유지 시간 \+\d/, `MPT ${mpt}초 단계 ${lv}`);
    }
  }
});

test('평균과 중앙값', () => {
  assert.equal(diffStats([]), null);
  const st = diffStats([1, 2, 9]);
  assert.ok(st && Math.abs(st.mean - 4) < 1e-9 && st.median === 2);
});

// ---- 촛불 색 ----

test('촛불 배경: 불이 약해질수록 어둡고, 꺼져도 완전히 캄캄하지 않아 초가 보인다', () => {
  const channel = (hex: string, i: number) => parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16);
  const brightness = (hex: string) => channel(hex, 0) + channel(hex, 1) + channel(hex, 2);
  assert.ok(brightness(sceneColor(1)) > brightness(sceneColor(0.5)));
  assert.ok(brightness(sceneColor(0.5)) > brightness(sceneColor(0)));
  assert.ok(brightness(sceneColor(0)) > 0, '검정이 아니다');
  assert.match(sceneColor(0), /^#[0-9a-f]{6}$/);
  assert.equal(sceneColor(-1), sceneColor(0));
  assert.equal(sceneColor(2), sceneColor(1));
  assert.ok(FLAME_OUT_BELOW > 0 && FLAME_OUT_BELOW < 0.2);
});

test('불꽃 색은 heat가 오를수록 온도가 높아지고 양 끝에서 범위를 벗어나지 않는다', () => {
  assert.equal(flameKelvin(0), 800);
  assert.equal(flameKelvin(1), 6500);
  assert.equal(flameKelvin(-1), 800);
  assert.equal(flameKelvin(2), 6500);
  assert.ok(flameKelvin(0.5) > flameKelvin(0.25));
  assert.match(flameColor(0.5), /^#[0-9a-f]{6}$/);
});
