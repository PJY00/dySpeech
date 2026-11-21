// 화면에서 쓰는 언어 훅. useLang()을 부른 화면은 언어가 바뀌면 다시 그려진다.
// 고른 언어는 기기에 저장해 앱을 다시 켜도 유지한다(처음에는 한국어).
import { useCallback, useEffect, useSyncExternalStore } from 'react';

import { stateStore } from '../session';
import { getLang, setLangValue, subscribeLang } from './lang';
import type { Lang } from './lang';

export { L, formatWhen, getLang } from './lang';
export type { Lang } from './lang';

const SETTINGS_DOC = 'app/settings.json';

interface SettingsDoc {
  lang?: Lang;
}

export function useLang(): { lang: Lang; setLang: (lang: Lang) => void } {
  const lang = useSyncExternalStore(subscribeLang, getLang, getLang);
  const setLang = useCallback((next: Lang) => {
    setLangValue(next);
    void (async () => {
      const doc = ((await stateStore.readJson(SETTINGS_DOC)) as SettingsDoc | null) ?? {};
      await stateStore.writeJson(SETTINGS_DOC, { ...doc, lang: next });
    })();
  }, []);
  return { lang, setLang };
}

/** 앱 시작 때 한 번: 저장된 언어를 불러온다 */
export function useLoadLang(): void {
  useEffect(() => {
    void (async () => {
      const doc = (await stateStore.readJson(SETTINGS_DOC)) as SettingsDoc | null;
      if (doc?.lang === 'ko' || doc?.lang === 'en') setLangValue(doc.lang);
    })();
  }, []);
}
