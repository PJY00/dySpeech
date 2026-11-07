import assert from 'node:assert/strict';
import { test } from 'node:test';

import { isUuidV4, uuidv4 } from '../uuid';

test('UUID v4 형식(버전 4, 변형 10xx)이고 겹치지 않는다', () => {
  const seen = new Set<string>();
  for (let i = 0; i < 2000; i++) {
    const id = uuidv4();
    assert.ok(isUuidV4(id), id);
    seen.add(id);
  }
  assert.equal(seen.size, 2000);
});

test('crypto가 없어도(Math.random 경로) v4 형식이다', () => {
  const id = uuidv4((bytes) => bytes.fill(0xff));
  assert.equal(id, 'ffffffff-ffff-4fff-bfff-ffffffffffff');
  assert.ok(isUuidV4(id));
  assert.ok(!isUuidV4('3f9b2c1e-7a4d-1e8b-9c2f-1a6d5e8b7c40'));
});
