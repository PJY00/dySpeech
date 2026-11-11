// 세션 상태를 기기에 저장하는 곳. 앱은 store.expo.ts(expo-file-system), 테스트는 MemoryStore나 testing/fsStore.ts를 쓴다.
// 경로는 저장소 안의 상대 경로(예: "sessions/<id>.json"). 녹음 파일은 녹음기가 만든 URI를 그대로 쓴다.

export interface Store {
  /** JSON 문서를 읽는다. 없으면 null */
  readJson(path: string): Promise<unknown | null>;
  writeJson(path: string, value: unknown): Promise<void>;
  remove(path: string): Promise<void>;
  /** 폴더 안 파일 이름 목록(하위 폴더 제외). 폴더가 없으면 빈 목록 */
  list(dir: string): Promise<string[]>;
  /** 녹음 파일 바이트 */
  readFile(uri: string): Promise<Uint8Array>;
  /** 녹음 파일 지우기. 없으면 아무것도 하지 않음 */
  deleteFile(uri: string): Promise<void>;
}

/** 메모리 저장소(테스트용). files에 녹음 바이트를 넣어 두고 쓴다 */
export class MemoryStore implements Store {
  readonly docs = new Map<string, string>();
  readonly files = new Map<string, Uint8Array>();

  async readJson(path: string): Promise<unknown | null> {
    const text = this.docs.get(path);
    return text === undefined ? null : JSON.parse(text);
  }

  async writeJson(path: string, value: unknown): Promise<void> {
    this.docs.set(path, JSON.stringify(value));
  }

  async remove(path: string): Promise<void> {
    this.docs.delete(path);
  }

  async list(dir: string): Promise<string[]> {
    const prefix = dir.replace(/\/+$/, '') + '/';
    return [...this.docs.keys()]
      .filter((k) => k.startsWith(prefix) && !k.slice(prefix.length).includes('/'))
      .map((k) => k.slice(prefix.length));
  }

  async readFile(uri: string): Promise<Uint8Array> {
    const bytes = this.files.get(uri);
    if (!bytes) throw new Error(`파일이 없습니다: ${uri}`);
    return bytes;
  }

  async deleteFile(uri: string): Promise<void> {
    this.files.delete(uri);
  }
}
