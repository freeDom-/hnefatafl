import { useLayoutEffect, useMemo, useSyncExternalStore, type ReactNode } from 'react';
import german from './locales/de';
import extra from './locales/de-extra';
import tournaments from './locales/de-tournaments';
import appearance from './locales/de-appearance';
import training from './locales/de-training';
import resume from './locales/de-resume';
import modelManagement from './locales/de-model-management';

export type Language = 'en' | 'de';
export type TranslationValues = Record<string, string | number>;
export type Translate = (source: string, values?: TranslationValues) => string;
export const LANGUAGE_KEY = 'hnefatafl-language';
const dictionary = {
  ...german,
  ...extra,
  ...tournaments,
  ...appearance,
  ...training,
  ...resume,
  ...modelManagement,
};
let current: Language = 'en';
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const snapshot = () => current;

export function readLanguage(): Language {
  try {
    const stored = localStorage.getItem(LANGUAGE_KEY);
    if (stored === 'en' || stored === 'de') return stored;
  } catch {
    // The selected language still works when browser storage is unavailable.
  }
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('de')
    ? 'de'
    : 'en';
}

export function setLanguage(language: Language, persist = true) {
  current = language;
  if (typeof document !== 'undefined') document.documentElement.lang = language;
  if (persist) {
    try {
      localStorage.setItem(LANGUAGE_KEY, language);
    } catch {
      // Keep the choice for this visit.
    }
  }
  listeners.forEach((listener) => listener());
}

export function getLocale(language: Language = current) {
  return language === 'de' ? 'de-DE' : 'en-GB';
}

export function translate(
  source: string,
  values: TranslationValues = {},
  language: Language = current,
): string {
  const key = source.trim();
  const translated =
    language === 'de'
      ? (dictionary[source] ??
        (dictionary[key]
          ? `${source.slice(0, source.indexOf(key))}${dictionary[key]}${source.slice(source.indexOf(key) + key.length)}`
          : source))
      : source;
  return translated.replace(/\{(\w+)\}/g, (placeholder, name: string) =>
    Object.hasOwn(values, name) ? String(values[name]) : placeholder,
  );
}

/** Display-only helpers use the same locale as React. Never translate saved identities. */
export const t: Translate = (source, values) => translate(source, values);

export function useI18n() {
  const language = useSyncExternalStore(subscribe, snapshot, () => 'en' as const);
  return useMemo(
    () => ({
      language,
      locale: getLocale(language),
      setLanguage,
      t: ((source, values) => translate(source, values, language)) as Translate,
    }),
    [language],
  );
}

export function I18nProvider({ children }: { children: ReactNode }) {
  useLayoutEffect(() => {
    setLanguage(readLanguage(), false);
    const changed = (event: StorageEvent) => {
      if (event.key === LANGUAGE_KEY) setLanguage(readLanguage(), false);
    };
    window.addEventListener('storage', changed);
    return () => {
      window.removeEventListener('storage', changed);
      current = 'en';
    };
  }, []);
  return children;
}

export function LanguageSelect() {
  const { t, language, setLanguage } = useI18n();
  return (
    <label className="language-select">
      <span className="sr-only">{t('Language')}</span>
      <select
        aria-label={t('Language')}
        value={language}
        onChange={(event) => setLanguage(event.target.value === 'de' ? 'de' : 'en')}
      >
        <option value="en" lang="en">
          English
        </option>
        <option value="de" lang="de">
          Deutsch
        </option>
      </select>
    </label>
  );
}
