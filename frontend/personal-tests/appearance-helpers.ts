import { expect, type Page } from '@playwright/test';
import catalog from '../src/appearanceCatalog.json' with { type: 'json' };
import type { Density, UiTheme } from '../src/appearanceConfig';
import { LANGUAGE_KEY, type Language } from '../src/i18n';

// The browser key and automatic rule are the same persisted contract consumed by
// appearanceConfig; importing that module directly is not possible in Node ESM
// because it imports the JSON catalog without an import attribute.
const APPEARANCE_KEY = 'hnefatafl-appearance';

export type AppearanceChoice = {
  uiTheme: UiTheme;
  language: Language;
  /** Absent deliberately selects the viewport-aware automatic scale. */
  density?: Density;
};

/** Apply the same persisted appearance/language settings the application reads. */
export async function applyAppearance(page: Page, choice: AppearanceChoice) {
  await page.evaluate(
    ({ appearanceKey, languageKey, choice }) => {
      localStorage.setItem(
        appearanceKey,
        JSON.stringify({
          version: 2,
          uiTheme: choice.uiTheme,
          ...(choice.density ? { density: choice.density } : {}),
        }),
      );
      localStorage.setItem(languageKey, choice.language);
    },
    { appearanceKey: APPEARANCE_KEY, languageKey: LANGUAGE_KEY, choice },
  );
}

/** Resolve the expected scale with the production utility, then assert what rendered. */
export async function expectAppliedAppearance(page: Page, choice: AppearanceChoice) {
  const largeViewport = await page.evaluate(
    (query) => window.matchMedia(query).matches,
    catalog.largeViewport,
  );
  // Mirrors effectiveDensity: an explicit choice wins; otherwise the catalog's
  // large-viewport query selects Large. The rendered data-density is asserted
  // below, so changing the viewport is never assumed to change the scale.
  const expectedDensity = choice.density ?? (largeViewport ? 'large' : 'default');
  await expect(page.locator('html')).toHaveAttribute('data-ui-theme', choice.uiTheme);
  await expect(page.locator('html')).toHaveAttribute('data-density', expectedDensity);
  await expect(page.locator('html')).toHaveAttribute('lang', choice.language);
  return expectedDensity;
}

export async function selectLanguage(page: Page, language: Language) {
  await page.locator('.language-select select').selectOption(language);
  await expect(page.locator('html')).toHaveAttribute('lang', language);
}
