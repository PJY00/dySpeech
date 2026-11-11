// expo-file-system(58.0.13)으로 만든 저장소. 상태 문서는 문서 폴더의 dyspeech/state/ 아래에 둔다.

import { Directory, File, Paths } from 'expo-file-system';

import { RECORDINGS_SUBDIR } from './store';
import type { Store } from './store';

const ROOT = ['dyspeech', 'state'];

function stateFile(path: string): File {
  return new File(Paths.document, ...ROOT, ...path.split('/'));
}

function ensureDir(dir: Directory): void {
  if (!dir.exists) dir.create({ intermediates: true });
}

export class ExpoStore implements Store {
  async readJson(path: string): Promise<unknown | null> {
    const file = stateFile(path);
    if (!file.exists) return null;
    const text = await file.text();
    return text ? JSON.parse(text) : null;
  }

  async writeJson(path: string, value: unknown): Promise<void> {
    const file = stateFile(path);
    ensureDir(file.parentDirectory);
    // 쓰다가 앱이 꺼져도 앞 내용이 남도록, 임시 파일에 쓴 뒤 바꿔 넣는다.
    const tmp = new File(file.parentDirectory, `${file.name}.tmp`);
    if (tmp.exists) tmp.delete();
    await tmp.write(JSON.stringify(value));
    if (file.exists) file.delete();
    await tmp.move(file);
  }

  async remove(path: string): Promise<void> {
    const file = stateFile(path);
    if (file.exists) file.delete();
  }

  async list(dir: string): Promise<string[]> {
    const d = new Directory(Paths.document, ...ROOT, ...dir.split('/').filter(Boolean));
    if (!d.exists) return [];
    return d
      .list()
      .filter((entry): entry is File => entry instanceof File)
      .map((f) => f.name)
      .filter((name) => !name.endsWith('.tmp'));
  }

  async readFile(uri: string): Promise<Uint8Array> {
    return new File(uri).bytes();
  }

  async deleteFile(uri: string): Promise<void> {
    const file = new File(uri);
    if (file.exists) file.delete();
  }

  async listFiles(dirUri: string): Promise<string[]> {
    const dir = new Directory(dirUri);
    if (!dir.exists) return [];
    return dir
      .list()
      .filter((entry): entry is File => entry instanceof File)
      .map((f) => f.uri);
  }
}

/** 녹음 파일 폴더 URI. 안드로이드는 녹음기(Document)와 expo-file-system(Paths.document) 모두 앱의 filesDir */
export function recordingsDirUri(): string {
  return new Directory(Paths.document, ...RECORDINGS_SUBDIR.split('/')).uri;
}
