// WAV 헤더 검사, 16비트 PCM 읽기·만들기.
// 검사 기준은 서버(backend/app/storage/wav.py)와 같다: 16비트 PCM, 모노, 허용 샘플레이트.

export const ALLOWED_SAMPLE_RATES = [16000, 44100, 48000] as const;
export const MAX_DURATION_S = 120;

const PCM = 1;
const EXTENSIBLE = 0xfffe;
const FORMAT_NAMES: Record<number, string> = { 3: '부동소수점', 6: 'A-law', 7: 'μ-law' };

export interface WavInfo {
  /** 형식 코드. 확장 형식(0xFFFE)이면 SubFormat의 형식 코드 */
  formatCode: number;
  channels: number;
  sampleRate: number;
  bitsPerSample: number;
  /** data 청크 본문 시작 위치(바이트) */
  dataOffset: number;
  /** data 청크 본문 길이(바이트). 헤더 값이 파일보다 크면 파일 끝까지 */
  dataBytes: number;
  /** 파일 전체 길이(바이트) */
  sizeBytes: number;
  /** 길이(초) = 표본 수 ÷ 샘플레이트 */
  durationS: number;
}

export class WavError extends Error {}

function ascii(bytes: Uint8Array, offset: number, length: number): string {
  let s = '';
  for (let i = 0; i < length; i++) s += String.fromCharCode(bytes[offset + i]);
  return s;
}

export function parseWav(bytes: Uint8Array): WavInfo {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length < 12 || ascii(bytes, 0, 4) !== 'RIFF' || ascii(bytes, 8, 4) !== 'WAVE') {
    throw new WavError('WAV 파일이 아닙니다(RIFF/WAVE 머리글이 없음)');
  }
  let fmt: { formatCode: number; channels: number; sampleRate: number; bitsPerSample: number } | null = null;
  let offset = 12;
  while (offset + 8 <= bytes.length) {
    const id = ascii(bytes, offset, 4);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === 'fmt ') {
      if (size < 16 || body + 16 > bytes.length) throw new WavError('fmt 정보가 잘려 있습니다');
      let formatCode = view.getUint16(body, true);
      if (formatCode === EXTENSIBLE) {
        if (size < 40 || body + 40 > bytes.length) throw new WavError('확장 fmt 정보가 잘려 있습니다');
        formatCode = view.getUint16(body + 24, true);
      }
      fmt = {
        formatCode,
        channels: view.getUint16(body + 2, true),
        sampleRate: view.getUint32(body + 4, true),
        bitsPerSample: view.getUint16(body + 14, true),
      };
    } else if (id === 'data') {
      if (!fmt) throw new WavError('data 앞에 fmt 정보가 없습니다');
      const dataBytes = Math.min(size, bytes.length - body);
      const frameBytes = Math.max(1, fmt.channels * Math.ceil(fmt.bitsPerSample / 8));
      const frames = Math.floor(dataBytes / frameBytes);
      return {
        ...fmt,
        dataOffset: body,
        dataBytes,
        sizeBytes: bytes.length,
        durationS: fmt.sampleRate > 0 ? frames / fmt.sampleRate : 0,
      };
    }
    offset = body + size + (size % 2);
  }
  throw new WavError(fmt ? 'data 청크가 없습니다' : 'fmt 정보를 찾지 못했습니다');
}

/** 녹음 규격(api.md 5.1)과 어긋나는 점. 맞으면 빈 목록 */
export function wavProblems(info: WavInfo): string[] {
  const problems: string[] = [];
  if (info.formatCode !== PCM) {
    const name = FORMAT_NAMES[info.formatCode] ?? `형식 코드 ${info.formatCode}`;
    problems.push(`PCM이 아닙니다(${name}). 16비트 PCM이어야 합니다`);
  }
  if (info.channels !== 1) problems.push(`채널이 ${info.channels}개입니다. 모노(1개)여야 합니다`);
  if (info.bitsPerSample !== 16) problems.push(`${info.bitsPerSample}비트입니다. 16비트여야 합니다`);
  if (!(ALLOWED_SAMPLE_RATES as readonly number[]).includes(info.sampleRate)) {
    problems.push(`샘플레이트 ${info.sampleRate} Hz는 받지 않습니다. ${ALLOWED_SAMPLE_RATES.join(', ')} 중 하나여야 합니다`);
  }
  if (info.durationS <= 0) problems.push('녹음 길이가 0초입니다');
  if (info.durationS > MAX_DURATION_S) problems.push(`녹음이 ${info.durationS.toFixed(1)}초입니다. 120초 이하여야 합니다`);
  return problems;
}

/** 16비트 PCM 모노 WAV의 표본을 −1~1 실수로 읽는다 */
export function decodePcm16(bytes: Uint8Array, info: WavInfo): Float32Array {
  if (info.bitsPerSample !== 16 || info.channels !== 1) throw new WavError('16비트 모노 WAV만 읽습니다');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const count = Math.floor(info.dataBytes / 2);
  const out = new Float32Array(count);
  for (let i = 0; i < count; i++) out[i] = view.getInt16(info.dataOffset + i * 2, true) / 32768;
  return out;
}

/** −1~1 실수 표본을 16비트 PCM 모노 WAV(44바이트 머리글)로 만든다. 범위 밖 값은 자른다 */
export function encodeWav(samples: ArrayLike<number>, sampleRate: number): Uint8Array {
  const dataBytes = samples.length * 2;
  const bytes = new Uint8Array(44 + dataBytes);
  const view = new DataView(bytes.buffer);
  const write = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) bytes[offset + i] = text.charCodeAt(i);
  };
  write(0, 'RIFF');
  view.setUint32(4, 36 + dataBytes, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, PCM, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, dataBytes, true);
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, Math.round(x < 0 ? x * 32768 : x * 32767), true);
  }
  return bytes;
}
