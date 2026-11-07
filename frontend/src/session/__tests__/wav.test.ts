import assert from 'node:assert/strict';
import { test } from 'node:test';

import { decodePcm16, encodeWav, parseWav, wavProblems } from '../wav';

function header(fields: { format?: number; channels?: number; rate?: number; bits?: number; extensible?: boolean; dataBytes?: number }) {
  const { format = 1, channels = 1, rate = 48000, bits = 16, extensible = false, dataBytes = 9600 } = fields;
  const fmtSize = extensible ? 40 : 16;
  const bytes = new Uint8Array(12 + 8 + fmtSize + 8 + dataBytes);
  const view = new DataView(bytes.buffer);
  const put = (o: number, s: string) => [...s].forEach((c, i) => (bytes[o + i] = c.charCodeAt(0)));
  put(0, 'RIFF');
  view.setUint32(4, bytes.length - 8, true);
  put(8, 'WAVE');
  put(12, 'fmt ');
  view.setUint32(16, fmtSize, true);
  view.setUint16(20, extensible ? 0xfffe : format, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, rate, true);
  view.setUint16(34, bits, true);
  if (extensible) view.setUint16(20 + 24, format, true);
  const data = 20 + fmtSize;
  put(data, 'data');
  view.setUint32(data + 4, dataBytes, true);
  return bytes;
}

test('만든 WAV를 다시 읽으면 형식·길이·표본이 같다', () => {
  const samples = Float32Array.from({ length: 4800 }, (_, i) => Math.sin(i / 10) * 0.5);
  const bytes = encodeWav(samples, 48000);
  const info = parseWav(bytes);
  assert.equal(info.formatCode, 1);
  assert.equal(info.channels, 1);
  assert.equal(info.sampleRate, 48000);
  assert.equal(info.bitsPerSample, 16);
  assert.equal(info.dataOffset, 44);
  assert.equal(info.sizeBytes, 44 + 9600);
  assert.equal(info.durationS, 0.1);
  assert.deepEqual(wavProblems(info), []);
  const back = decodePcm16(bytes, info);
  assert.equal(back.length, samples.length);
  for (let i = 0; i < samples.length; i += 97) assert.ok(Math.abs(back[i] - samples[i]) < 1 / 16000);
});

test('규격과 다르면 이유를 모두 돌려준다(서버 문구와 같음)', () => {
  const problems = wavProblems(parseWav(header({ channels: 2, rate: 22050, bits: 24, format: 3 })));
  assert.deepEqual(problems, [
    'PCM이 아닙니다(부동소수점). 16비트 PCM이어야 합니다',
    '채널이 2개입니다. 모노(1개)여야 합니다',
    '24비트입니다. 16비트여야 합니다',
    '샘플레이트 22050 Hz는 받지 않습니다. 16000, 44100, 48000 중 하나여야 합니다',
  ]);
});

test('확장 형식(0xFFFE)은 SubFormat의 형식 코드로 판단한다', () => {
  assert.deepEqual(wavProblems(parseWav(header({ extensible: true, format: 1 }))), []);
  assert.equal(parseWav(header({ extensible: true, format: 3 })).formatCode, 3);
});

test('잘린 파일: data 크기가 파일보다 크면 파일 끝까지로 본다', () => {
  const bytes = header({ dataBytes: 9600 }).slice(0, 44 + 4800);
  const info = parseWav(bytes);
  assert.equal(info.dataBytes, 4800);
  assert.equal(info.durationS, 0.05);
});

test('WAV가 아니거나 data가 없으면 WavError', () => {
  assert.throws(() => parseWav(new Uint8Array(10)), /RIFF\/WAVE/);
  assert.throws(() => parseWav(header({}).slice(0, 36)), /data 청크가 없습니다/);
  assert.match(wavProblems(parseWav(header({ dataBytes: 0 }))).join(), /0초/);
});
