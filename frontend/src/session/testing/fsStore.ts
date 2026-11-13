// Node 파일 시스템 저장소(통합 시험용). 녹음 URI는 file:// 경로.

import { mkdir, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import type { Store } from '../store';

export class FsStore implements Store {
  private readonly root: string;

  constructor(root: string) {
    this.root = root;
  }

  async readJson(path: string): Promise<unknown | null> {
    try {
      return JSON.parse(await readFile(join(this.root, path), 'utf8'));
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === 'ENOENT') return null;
      throw e;
    }
  }

  async writeJson(path: string, value: unknown): Promise<void> {
    const full = join(this.root, path);
    await mkdir(dirname(full), { recursive: true });
    await writeFile(full, JSON.stringify(value));
  }

  async remove(path: string): Promise<void> {
    await rm(join(this.root, path), { force: true });
  }

  async list(dir: string): Promise<string[]> {
    try {
      const entries = await readdir(join(this.root, dir), { withFileTypes: true });
      return entries.filter((e) => e.isFile()).map((e) => e.name);
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === 'ENOENT') return [];
      throw e;
    }
  }

  async readFile(uri: string): Promise<Uint8Array> {
    return new Uint8Array(await readFile(fileURLToPath(uri)));
  }

  async deleteFile(uri: string): Promise<void> {
    await rm(fileURLToPath(uri), { force: true });
  }

  async listFiles(dirUri: string): Promise<string[]> {
    const dir = fileURLToPath(dirUri);
    try {
      const entries = await readdir(dir, { withFileTypes: true });
      return entries.filter((e) => e.isFile()).map((e) => pathToFileURL(join(dir, e.name)).href);
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === 'ENOENT') return [];
      throw e;
    }
  }
}
