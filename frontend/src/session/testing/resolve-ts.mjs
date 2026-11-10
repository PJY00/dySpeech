// node:test가 앱 코드처럼 확장자 없는 import('./api')를 .ts 파일로 찾게 하는 해석 훅.
// 사용: node --import ./src/session/testing/resolve-ts.mjs --test "src/**/*.test.ts"
import { registerHooks } from 'node:module';

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
