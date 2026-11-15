// 앱의 판정 코드(frontend/src의 TypeScript)를 고치지 않고 그대로 불러, 발화 조각마다 기기 방식 값을 계산한다.
// analysis/game_levels.py가 실행한다. Node.js 24(TypeScript를 바로 읽음)와 frontend의 node_modules(pitchy)가 필요하다.
//
// 표준 입력으로 JSON 하나를 받고, 표준 출력으로 JSON 하나를 낸다.
// 입력: { frontend, file, rate, min_hz, max_hz, lead_s, clips: [{ offset, length }], floor }
//   frontend: frontend 폴더의 절대 경로
//   file: 조각들을 이어 붙인 모노 표본 파일(32비트 실수, 리틀 엔디언). offset·length는 표본 수
//   rate: 샘플레이트(Hz), min_hz·max_hz: 기본주파수 탐색 범위(Hz)
//   lead_s: 조각 앞에 붙인 조용한 부분의 길이(초). 이 부분의 음량을 따로 잰다
//   floor: 잡음 바닥(dBFS). 앱에서는 측정 블록의 잡음 바닥이 들어가는 자리다
// 출력: { guide_levels, up_at_pct, down_at_pct, clips: [...] }
//   guide_levels: 노래방 따라 읽기 단계표 [[단계, 허용 폭(%), 변동계수 기준], ...] (scoring.ts의 LEVEL_SPECS)
//   up_at_pct·down_at_pct: 단계를 올리고 내리는 라운드 성공률(%)
//   clips[i]: { speech_s, cv, nuclei, lead_db, t0_s, hop_s, hz }
//     speech_s: 휴지를 뺀 발화 시간(초), cv: 음절 간격 변동계수(구하지 못하면 null), nuclei: 찾은 음절 핵 수
//       → 노래방 따라 읽기의 analyzeSentence 결과 그대로
//     lead_db: 조각 앞 lead_s초의 프레임 음량 중앙값(dBFS, 앱의 말소리 대역 필터를 거친 값). 프레임이 없으면 null
//     t0_s·hop_s·hz: 우주선 게임의 실시간 기본주파수 추적기(PitchTracker) 결과. 프레임 i의 시각 = t0_s + i × hop_s(조각 시작 기준, 초),
//       hz[i]는 기본주파수(Hz, 못 잡으면 null). 게임과 같이 약 8 kHz로 줄인 표본을 0.02초 간격으로 넣는다

import { readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// 앱 코드는 확장자 없이 import('./nuclei')처럼 쓴다. 그런 상대 경로를 .ts 파일로 찾게 한다(앱 테스트가 쓰는 방법과 같다).
registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context);
    } catch (error) {
      const relative = specifier.startsWith('./') || specifier.startsWith('../');
      if (relative && !/\.[cm]?[jt]sx?$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
      throw error;
    }
  },
});

const input = JSON.parse(readFileSync(0, 'utf8'));
const load = (path) => import(pathToFileURL(join(input.frontend, 'src', path)).href);

const { analyzeSentence } = await load('games/guide_reading/analysis.ts');
const { LEVEL_SPECS, UP_AT_PCT, DOWN_AT_PCT } = await load('games/guide_reading/scoring.ts');
const { frameLevels, median } = await load('measurement/analysis.ts');
const { voiceBand } = await load('measurement/filters.ts');
const { PitchTracker, decimate, REALTIME_HOP_SEC, REALTIME_PITCH_RATE } = await load('games/spaceship/audio/PitchTracker.ts');

const round = (value, digits) => (value === null || !Number.isFinite(value) ? null : Number(value.toFixed(digits)));

/** 조각 하나를 앱의 문장 분석과 우주선 기본주파수 추적기로 계산한다 */
function measure(samples) {
  const rate = input.rate;
  // 음절 수는 여기서 쓰지 않는다(말속도는 game_levels.py가 음절 수 ÷ speech_s로 구한다). 1을 넣어 발화 시간만 받는다
  const sentence = analyzeSentence(samples, rate, 1, {
    noiseFloorDbfs: input.floor,
    searchRange: { min: input.min_hz, max: input.max_hz },
  });

  const lead = samples.subarray(0, Math.min(samples.length, Math.round(input.lead_s * rate)));
  const leadLevels = lead.length ? frameLevels(voiceBand(lead, rate), rate) : [];

  // 게임(useSpaceshipMic)과 같은 설정: 약 8 kHz로 줄이고 0.02초 간격. 음량은 말소리 대역 필터를 거친 표본에서 잰다
  const factor = Math.max(1, Math.round(rate / REALTIME_PITCH_RATE));
  const tracker = new PitchTracker(rate / factor, { minHz: input.min_hz, maxHz: input.max_hz, hopSec: REALTIME_HOP_SEC });
  const frames = tracker.push(decimate(samples, factor), decimate(voiceBand(samples, rate), factor));

  return {
    speech_s: round(sentence.speechS, 3),
    cv: round(sentence.cv, 4),
    nuclei: sentence.detected,
    lead_db: leadLevels.length ? round(median(leadLevels), 2) : null,
    t0_s: tracker.windowSize / 2 / tracker.sampleRate,
    hop_s: tracker.hopSize / tracker.sampleRate,
    hz: frames.map((frame) => round(frame.hz, 2)),
  };
}

const bytes = input.clips.length ? readFileSync(input.file) : Buffer.alloc(0);
const clips = input.clips.map(({ offset, length }) => {
  // 파일 버퍼는 4바이트 경계에 놓이지 않을 수 있어 복사해서 쓴다
  const start = bytes.byteOffset + offset * 4;
  return measure(new Float32Array(bytes.buffer.slice(start, start + length * 4)));
});

process.stdout.write(
  JSON.stringify({
    guide_levels: LEVEL_SPECS.map((spec) => [spec.level, spec.tolerancePct, spec.cvMax]),
    up_at_pct: UP_AT_PCT,
    down_at_pct: DOWN_AT_PCT,
    clips,
  }),
);
