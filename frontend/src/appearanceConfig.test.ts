import { describe, expect, it } from 'vitest';
import { DEFAULT_APPEARANCE, UI_THEMES, parseAppearance, resolveUiTheme } from './appearanceConfig';

describe('uiTheme preference', () => {
  it('lists the three frozen theme ids', () => {
    expect(UI_THEMES.map((theme) => theme.id)).toEqual(['kings-table', 'iron-ash', 'saga-hall']);
  });

  it('keeps an explicit valid uiTheme alongside the untouched legacy fields', () => {
    expect(parseAppearance({ uiTheme: 'saga-hall' })).toStrictEqual({
      ...DEFAULT_APPEARANCE,
      uiTheme: 'saga-hall',
    });
    expect(parseAppearance({ uiTheme: 'iron-ash', mode: 'dark', tokens: 'classic' })).toStrictEqual(
      {
        ...DEFAULT_APPEARANCE,
        uiTheme: 'iron-ash',
        mode: 'dark',
        tokens: 'classic',
      },
    );
  });

  it('omits uiTheme when absent or invalid so existing storage keeps its meaning', () => {
    expect(parseAppearance({ mode: 'wood' })).toStrictEqual({
      ...DEFAULT_APPEARANCE,
      mode: 'wood',
    });
    expect(parseAppearance({ uiTheme: 'not-a-theme' })).toStrictEqual(DEFAULT_APPEARANCE);
    expect(parseAppearance({ uiTheme: 42 })).toStrictEqual(DEFAULT_APPEARANCE);
    expect(parseAppearance(null)).toStrictEqual(DEFAULT_APPEARANCE);
  });

  it('resolves the picker default without applying it', () => {
    expect(resolveUiTheme(DEFAULT_APPEARANCE)).toBe('kings-table');
    expect(resolveUiTheme({ ...DEFAULT_APPEARANCE, uiTheme: 'iron-ash' })).toBe('iron-ash');
  });
});
