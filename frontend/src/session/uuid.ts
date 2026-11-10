// UUID v4를 JS로 만든다. 새 네이티브 패키지 없이 쓰기 위해서다.
// crypto.getRandomValues가 있으면 쓰고, 없으면(Hermes 기본 상태) Math.random으로 채운다.

type RandomFill = (bytes: Uint8Array) => void;

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function defaultFill(bytes: Uint8Array): void {
  const c = (globalThis as { crypto?: { getRandomValues?: (a: Uint8Array) => Uint8Array } }).crypto;
  if (c?.getRandomValues) {
    c.getRandomValues(bytes);
    return;
  }
  for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
}

export function uuidv4(fill: RandomFill = defaultFill): string {
  const bytes = new Uint8Array(16);
  fill(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function isUuidV4(value: string): boolean {
  return UUID_V4.test(value);
}
