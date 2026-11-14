import { ARENA_DECOR } from './arenaAssets';
import {
  createContext,
  useContext,
  useId,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import {
  APPEARANCE_KEY,
  BOARD_THEMES,
  DEFAULT_APPEARANCE,
  DENSITIES,
  LARGE_VIEWPORT_QUERY,
  effectiveDensity,
  type Density,
  TOKEN_THEMES,
  UI_THEMES,
  parseAppearance,
  resolveUiTheme,
  type Appearance,
} from './appearanceConfig';
import { Token } from './Tokens';
import { useI18n } from './i18n';

interface AppearanceContext {
  appearance: Appearance;
  density: Density;
  updateAppearance: (patch: Partial<Appearance>) => void;
  canPersist: boolean;
}
const AppearanceContext = createContext<AppearanceContext>({
  appearance: DEFAULT_APPEARANCE,
  density: 'default',
  updateAppearance: () => {},
  canPersist: true,
});
export const useAppearance = () => useContext(AppearanceContext);

function storedAppearance(): Appearance {
  try {
    return parseAppearance(JSON.parse(localStorage.getItem(APPEARANCE_KEY) ?? 'null'));
  } catch {
    return { ...DEFAULT_APPEARANCE };
  }
}
function largeViewport() {
  return typeof window.matchMedia === 'function' && window.matchMedia(LARGE_VIEWPORT_QUERY).matches;
}
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [appearance, setAppearance] = useState<Appearance>(storedAppearance);
  const [large, setLarge] = useState(largeViewport);
  const [canPersist, setCanPersist] = useState(true);
  const density = effectiveDensity(appearance, large);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const preference = window.matchMedia(LARGE_VIEWPORT_QUERY);
    const changed = () => setLarge(preference.matches);
    changed();
    preference.addEventListener('change', changed);
    return () => preference.removeEventListener('change', changed);
  }, []);
  useLayoutEffect(() => {
    const root = document.documentElement;
    for (const [name, asset] of Object.entries(ARENA_DECOR))
      root.style.setProperty(`--arena-${name}-art`, `url("${asset.path}")`);
    delete root.dataset.colorMode;
    root.dataset.density = density;
    root.dataset.tokenTheme = appearance.tokens;
    root.dataset.boardTheme = appearance.board;
    root.dataset.uiTheme = appearance.uiTheme;
    root.style.removeProperty('color-scheme');
    const board = BOARD_THEMES.find((theme) => theme.id === appearance.board)!;
    for (const [key, value] of Object.entries(board.palette))
      root.style.setProperty(
        `--board-${key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`,
        value,
      );
    try {
      const serialized = JSON.stringify(appearance);
      if (localStorage.getItem(APPEARANCE_KEY) !== serialized)
        localStorage.setItem(APPEARANCE_KEY, serialized);
      setCanPersist(true);
    } catch {
      setCanPersist(false);
    }
  }, [appearance, density]);
  return (
    <AppearanceContext.Provider
      value={{
        appearance,
        density,
        updateAppearance: (patch) =>
          setAppearance((current) => parseAppearance({ ...current, ...patch })),
        canPersist,
      }}
    >
      {children}
    </AppearanceContext.Provider>
  );
}

function AppearanceIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m12 2 10 10-10 10L2 12Z" stroke="currentColor" strokeWidth="1.6" />
      <path d="m12 6 5 6-5 6-5-6Z" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

export function AppearanceSettings() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else if (dialog.current?.open) dialog.current.close();
  }, [open]);
  return (
    <>
      <button
        className="appearance-trigger"
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t('Appearance settings')}
      >
        <AppearanceIcon />
        <span>{t('Appearance')}</span>
      </button>
      <dialog
        className="modal appearance-modal"
        ref={dialog}
        aria-label={t('Appearance settings')}
        onCancel={(event) => {
          event.preventDefault();
          setOpen(false);
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) setOpen(false);
        }}
      >
        <div className="modal-top">
          <div>
            <h2>{t('Make the table yours.')}</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label={t('Close appearance settings')}
            onClick={() => setOpen(false)}
          >
            ×
          </button>
        </div>
        <AppearancePreferences />
        <div className="appearance-footer">
          <button className="button primary" type="button" onClick={() => setOpen(false)}>
            {t('Back to the table')}
          </button>
        </div>
      </dialog>
    </>
  );
}

/** The same controls and browser persistence, also available directly in the profile. */
export function AppearancePreferences() {
  const { t } = useI18n();
  const { appearance, density: currentDensity, updateAppearance, canPersist } = useAppearance();
  const groupId = useId();
  return (
    <>
      <p className="appearance-intro">
        {t('Choose your theme, pieces and battlefield. Your games stay just as you left them.')}
      </p>
      <fieldset className="appearance-section">
        <legend>{t('UI theme')}</legend>
        <div className="appearance-choices appearance-themes">
          {UI_THEMES.map((theme) => (
            <label
              className={`appearance-choice ${
                resolveUiTheme(appearance) === theme.id ? 'chosen' : ''
              }`}
              key={theme.id}
            >
              <input
                type="radio"
                name={`${groupId}-ui-theme`}
                value={theme.id}
                checked={resolveUiTheme(appearance) === theme.id}
                onChange={() => updateAppearance({ uiTheme: theme.id })}
                aria-label={t(theme.name)}
              />
              <span className={`theme-preview theme-preview-${theme.id}`} aria-hidden="true" />
              <strong>{t(theme.name)}</strong>
              <small>{t(theme.description)}</small>
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="appearance-section">
        <legend>{t('Token theme')}</legend>
        <div className="appearance-choices">
          {TOKEN_THEMES.map((theme) => (
            <label
              className={`appearance-choice ${appearance.tokens === theme.id ? 'chosen' : ''}`}
              key={theme.id}
            >
              <input
                type="radio"
                name={`${groupId}-token-theme`}
                value={theme.id}
                checked={appearance.tokens === theme.id}
                onChange={() => updateAppearance({ tokens: theme.id })}
                aria-label={t(theme.name)}
              />
              <span className="token-preview">
                {['A', 'D', 'K'].map((piece) => (
                  <Token key={piece} piece={piece} theme={theme.id} />
                ))}
              </span>
              <strong>{t(theme.name)}</strong>
              <small>{t(theme.description)}</small>
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="appearance-section">
        <legend>{t('Board theme')}</legend>
        <div className="appearance-choices">
          {BOARD_THEMES.map((theme) => (
            <label
              className={`appearance-choice ${appearance.board === theme.id ? 'chosen' : ''}`}
              key={theme.id}
            >
              <input
                type="radio"
                name={`${groupId}-board-theme`}
                value={theme.id}
                checked={appearance.board === theme.id}
                onChange={() => updateAppearance({ board: theme.id })}
                aria-label={t(theme.name)}
              />
              <span
                className="board-preview"
                style={
                  {
                    '--preview-light': theme.palette.light,
                    '--preview-dark': theme.palette.dark,
                    '--preview-rim': theme.palette.frame,
                  } as CSSProperties
                }
              >
                {Array.from({ length: 9 }, (_, index) => (
                  <span key={index}>
                    {index === 1 ? (
                      <Token piece="A" theme={appearance.tokens} />
                    ) : index === 7 ? (
                      <Token piece="K" theme={appearance.tokens} />
                    ) : null}
                  </span>
                ))}
              </span>
              <strong>{t(theme.name)}</strong>
              <small>{t(theme.description)}</small>
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="appearance-section">
        <legend>{t('Interface scale')}</legend>
        <div className="appearance-choices">
          {DENSITIES.map((density) => (
            <label
              className={`appearance-choice ${currentDensity === density.id ? 'chosen' : ''}`}
              key={density.id}
            >
              <input
                type="radio"
                name={`${groupId}-density`}
                value={density.id}
                checked={currentDensity === density.id}
                onChange={() => updateAppearance({ density: density.id })}
                onClick={() => {
                  // Clicking the already-selected automatic value is still an
                  // explicit choice (radio onChange does not fire in that case).
                  if (appearance.density == null && currentDensity === density.id)
                    updateAppearance({ density: density.id });
                }}
                aria-label={t(density.name)}
              />
              <strong>{t(density.name)}</strong>
              <small>{t(density.description)}</small>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="appearance-footer">
        <p role="status">
          {canPersist
            ? t('Your choices are saved on this device.')
            : t('Your browser could not save these choices. They will apply for this visit.')}
        </p>
      </div>
    </>
  );
}
