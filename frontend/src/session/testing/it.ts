// 로컬 백엔드 통합 시험 도우미. 환경 변수는 __tests__/server.it.test.ts 맨 위 설명을 본다.

import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import { computeBlock, median } from '../../measurement/analysis';
import { checkRecording } from '../../measurement/checks';
import type { StepAnalysis } from '../../measurement/checks';
import type { StepId } from '../../measurement/steps';
import { ApiClient } from '../api';
import type { FetchLike } from '../api';
import { createSessionModule } from '../core';
import type { Session } from '../core';
import type { RecordingInput } from '../types';
import { encodeWav } from '../wav';
import { FsStore } from './fsStore';
import { measurementBlock, RATE } from './synth';

export const IT_URL = process.env.DYSPEECH_IT_URL;
export const IT_KEY = process.env.DYSPEECH_IT_KEY;
const CLEANUP = process.env.DYSPEECH_IT_CLEANUP ?? join(tmpdir(), 'dyspeech-it-cleanup.json');

export async function rememberForCleanup(userId: string): Promise<void> {
  let doc: { user_ids: string[]; session_ids: string[]; s3_keys: string[] } = { user_ids: [], session_ids: [], s3_keys: [] };
  try {
    doc = JSON.parse(await readFile(CLEANUP, 'utf8'));
  } catch {
    // 처음 만드는 파일
  }
  if (!doc.user_ids.includes(userId)) doc.user_ids.push(userId);
  await writeFile(CLEANUP, JSON.stringify(doc, null, 2));
}

/** 측정 블록 화면과 같은 순서로 녹음을 검사·추가하고 기기 지표·기준값을 저장한다 */
export async function runMeasurementBlock(session: Session, dir: string, seed: number): Promise<void> {
  const block = measurementBlock(seed);
  const steps: [StepId, Float32Array, (uri: string) => RecordingInput][] = [
    ['glide', block.glide, (fileUri) => ({ kind: 'calibration_glide', fileUri })],
    ['noise', block.noiseFloor, (fileUri) => ({ kind: 'noise_floor', fileUri })],
    ['mpt1', block.trials[0], (fileUri) => ({ kind: 'mpt_trial', trialNo: 1, fileUri })],
    ['mpt2', block.trials[1], (fileUri) => ({ kind: 'mpt_trial', trialNo: 2, fileUri })],
    ['mpt3', block.trials[2], (fileUri) => ({ kind: 'mpt_trial', trialNo: 3, fileUri })],
    ['sentence', block.sentence, (fileUri) => ({ kind: 'eval_sentence', sentenceId: 's1', fileUri })],
  ];
  const analyses = new Map<StepId, StepAnalysis>();
  let noiseFloor: number | null = null;
  let calibration = session.calibration();
  for (const [step, samples, input] of steps) {
    if (step === 'glide' && !session.needsCalibration) continue;
    const bytes = encodeWav(samples, RATE);
    const path = join(dir, `${step}-${seed}.wav`);
    await writeFile(path, bytes);
    const c = checkRecording(step, bytes, { noiseFloorDbfs: noiseFloor, calibration });
    assert.ok(c.ok && c.analysis, `${step}: ${c.message}`);
    await session.addRecording(input(pathToFileURL(path).href));
    analyses.set(step, c.analysis);
    if (step === 'noise') noiseFloor = median(c.analysis.levels);
    if (step === 'glide') calibration = c.analysis.calibration;
  }
  const sentence = analyses.get('sentence');
  const r = computeBlock({
    noise: analyses.get('noise')?.levels ?? null,
    trials: (['mpt1', 'mpt2', 'mpt3'] as StepId[]).map((s) => analyses.get(s)?.levels).filter((l): l is Float64Array => !!l),
    sentence: sentence ? { levels: sentence.levels, f0: sentence.f0 ?? [] } : null,
    calibration: session.needsCalibration ? calibration : null,
    storedCalibration: session.calibration(),
  });
  await session.setDeviceMeasurements(r.measurements);
  const glide = analyses.get('glide')?.calibration ?? null;
  await session.setBaselines({ ...r.baselines, sessionId: session.id, measuredAt: new Date().toISOString() }, glide ? { calibration: glide } : undefined);
}

export async function itModule(name: string) {
  const dir = await mkdtemp(join(tmpdir(), `dyspeech-it-${name}-`));
  const module = createSessionModule({
    store: new FsStore(join(dir, 'state')),
    api: new ApiClient({ baseUrl: IT_URL as string, appKey: IT_KEY as string, fetch: fetch as unknown as FetchLike }),
    apiConfigured: true,
    device: () => ({ device_model: 'node-integration-test', os_version: `node ${process.version}`, app_version: 'integration-test' }),
    log: (event, data) => {
      if (['send_start', 'send_done', 'send_failed', 'retry', 'restart', 'status'].includes(event)) console.log(`[session] ${event}`, JSON.stringify(data));
    },
  });
  return { dir, module };
}
