// 로컬 백엔드 통합 시험(환경 변수가 있을 때만 실행).
//   DYSPEECH_IT_URL      서버 주소(예: http://127.0.0.1:8000). 로컬 테스트 DB + 실제 S3로 띄운 서버
//   DYSPEECH_IT_KEY      앱 키(서버의 APP_API_KEY)
//   DYSPEECH_IT_CLEANUP  만든 사용자 ID를 적을 정리 목록 파일(backend/tools/cleanup.py 형식). 없으면 임시 폴더
// 실제 녹음과 같은 형식(모노·16비트·48 kHz WAV)의 합성 신호로 반복 측정(retest) 한 회차를 보내 done까지 확인한다.

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { FsStore } from '../testing/fsStore';
import { IT_KEY, IT_URL, itModule, rememberForCleanup, runMeasurementBlock } from '../testing/it';

test('로컬 백엔드: 반복 측정(retest) 한 회차가 실제 S3 업로드를 거쳐 done이 된다', { skip: !IT_URL || !IT_KEY ? 'DYSPEECH_IT_URL·DYSPEECH_IT_KEY 없음' : false, timeout: 300_000 }, async () => {
  const { dir, module } = await itModule('retest');
  const user = await module.startNewDevUser();
  await rememberForCleanup(user.userId);
  const session = await module.startSession('retest', { clientMeta: { source: 'integration_test' } });
  assert.equal(session.needsCalibration, true);
  await runMeasurementBlock(session, dir, 21);
  await session.complete({ clientMeta: { source: 'integration_test' } });
  const p = await session.waitForResult({ intervalMs: 3000, timeoutMs: 240_000 });
  assert.equal(p.state, 'done', JSON.stringify(p));
  assert.equal(p.uploaded, 6);
  const server = new Map(p.server?.server_measurements.map((m) => [m.metric as string, m.value]));
  const b = session.baselines();
  assert.ok(b);
  console.log('[it] user', user.userId, 'session', session.id);
  console.log('[it] server', JSON.stringify(p.server?.server_measurements.map((m) => [m.metric, Math.round(m.value * 100) / 100, m.low_reliability])));
  // 기기 값과 서버 값 비교: 같은 대역(60 Hz~8 kHz)·같은 정의라 거의 같아야 한다
  const pairs: [string, number | null, number][] = [
    ['noise_floor', b.noiseFloorDbfs, 1],
    ['sustained_level', b.sustainedLevelDbfs, 0.5],
    ['max_phonation_time', b.maxPhonationTimeS, 0.05],
    ['f0_median', b.f0MedianHz, (server.get('f0_median') ?? 0) * 0.03],
  ];
  for (const [metric, device, tol] of pairs) {
    const s = server.get(metric);
    assert.ok(s !== undefined && device !== null, `${metric}: 서버 ${s}, 기기 ${device}`);
    console.log(`[it] ${metric} 서버 ${s.toFixed(2)} 기기 ${device.toFixed(2)} 차이 ${(device - s).toFixed(2)}`);
    assert.ok(Math.abs(device - s) <= tol, `${metric} 차이 ${(device - s).toFixed(3)} > ${tol}`);
  }
  // 202 뒤 기기 복사 파일은 지운다
  const left = await new FsStore(dir).list('.');
  assert.deepEqual(left.filter((n) => n.endsWith('.wav')), []);
});
