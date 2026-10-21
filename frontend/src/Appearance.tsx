import {
  createContext,
  useContext,
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
  COLOR_MODES,
  DEFAULT_APPEARANCE,
  TOKEN_THEMES,
  parseAppearance,
  type Appearance,
  type ColorMode,
} from './appearanceConfig';
import { Token } from './Tokens';
import { useI18n } from './i18n';

interface AppearanceContext {
  appearance: Appearance;
  resolvedMode: Exclude<ColorMode, 'system'>;
  updateAppearance: (patch: Partial<Appearance>) => void;
  canPersist: boolean;
}
const AppearanceContext = createContext<AppearanceContext>({
  appearance: DEFAULT_APPEARANCE,
  resolvedMode: 'light',
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
function mediaPreference() {
  return typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;
}
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [appearance, setAppearance] = useState<Appearance>(storedAppearance);
  const [systemDark, setSystemDark] = useState(() => mediaPreference()?.matches ?? false);
  const [canPersist, setCanPersist] = useState(true);
  const resolvedMode =
    appearance.mode === 'system' ? (systemDark ? 'dark' : 'light') : appearance.mode;
  useEffect(() => {
    const preference = mediaPreference();
    if (!preference) return;
    const changed = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    setSystemDark(preference.matches);
    if (preference.addEventListener) preference.addEventListener('change', changed);
    else preference.addListener(changed);
    return () => {
      if (preference.removeEventListener) preference.removeEventListener('change', changed);
      else preference.removeListener(changed);
    };
  }, []);
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.colorMode = resolvedMode;
    root.dataset.tokenTheme = appearance.tokens;
    root.dataset.boardTheme = appearance.board;
    root.style.colorScheme = resolvedMode === 'wood' ? 'dark' : resolvedMode;
    const board = BOARD_THEMES.find((theme) => theme.id === appearance.board)!;
    for (const [key, value] of Object.entries(board.palette))
      root.style.setProperty(
        `--board-${key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`,
        value,
      );
    try {
      localStorage.setItem(APPEARANCE_KEY, JSON.stringify(appearance));
      setCanPersist(true);
    } catch {
      setCanPersist(false);
    }
  }, [appearance, resolvedMode]);
  return (
    <AppearanceContext.Provider
      value={{
        appearance,
        resolvedMode,
        updateAppearance: (patch) =>
          setAppearance((current) => parseAppearance({ ...current, ...patch })),
        canPersist,
      }}
    >
      {resolvedMode === 'wood' && <WoodOrnaments />}
      {children}
    </AppearanceContext.Provider>
  );
}

function WoodOrnaments() {
  return (
    <div className="wood-ornaments" data-wood-ornaments="true" aria-hidden="true">
      <svg className="wood-frieze" viewBox="0 0 520 128" fill="none">
        <path d="M5 96h510M5 101h510" stroke="currentColor" strokeWidth="1" />
        <g
          data-wood-motif="axes"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="m28 24 48 58m0-58L28 82" strokeWidth="3" />
          <path d="m25 20 21 3-12 20-15-9Zm54 0-21 3 12 20 15-9Z" strokeWidth="2" />
          <path d="m32 28 6 6m28 0 6-6" opacity=".55" />
        </g>
        <g data-wood-motif="shield" stroke="currentColor" strokeWidth="2">
          <path d="M145 23q26-11 52 0v26q-4 24-26 37-22-13-26-37Z" />
          <path d="M151 28q20-8 40 0v21q-4 18-20 30-16-12-20-30Z" opacity=".5" />
          <path d="M171 33v32m-10-24 10 10 10-10" strokeLinecap="round" strokeLinejoin="round" />
        </g>
        <g
          data-wood-motif="dragonboat"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M263 67q38 22 76 0l-9 17h-57Zm76 0 4-21 8-8-1-9-10-3-5 7 8 4m-80 30-7-14-6-3-3 3 2 6 10 4" />
          <path d="M300 16v58m0-56 28 13-28 23m3-33 18 11-18 16M277 84l-3 6m14-4-1 5m28-5 1 5m12-8 3 7" />
          <path d="M265 95q12-5 24 0t24 0 24 0" opacity=".6" />
          <circle cx="345" cy="31" r="1" fill="currentColor" stroke="none" />
        </g>
        <g data-wood-motif="hammer" stroke="currentColor" strokeWidth="2" strokeLinejoin="round">
          <path d="m431 25 15-9 15 9 13 2-4 23-16-3v33l-8 7-8-7V47l-16 3-4-23Z" />
          <path d="m431 31 15-8 15 8m-21 23 12 9-12 9m0-29 6-5 6 5" opacity=".6" />
        </g>
        <text
          x="260"
          y="121"
          textAnchor="middle"
          fill="currentColor"
          fontSize="15"
          letterSpacing="8"
        >
          ᛏ ᚱ ᛉ ᛟ ᚠ ᛞ ᚱ ᛏ
        </text>
      </svg>
    </div>
  );
}

function ModeIcon({ mode }: { mode: ColorMode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {mode === 'wood' ? (
        <>
          <path d="M5 5q7-4 14 0v7q-1 6-7 9-6-3-7-9Z" stroke="currentColor" strokeWidth="1.5" />
          <path
            d="M12 7v9m-3-7 3 3 3-3"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </>
      ) : mode === 'dark' ? (
        <path
          d="M19.7 15.1A8.3 8.3 0 0 1 8.9 4.3a8.3 8.3 0 1 0 10.8 10.8Z"
          stroke="currentColor"
          strokeWidth="1.7"
        />
      ) : mode === 'system' ? (
        <>
          <rect x="3" y="4" width="18" height="13" rx="2" stroke="currentColor" strokeWidth="1.7" />
          <path d="M8 21h8m-4-4v4M12 4v13" stroke="currentColor" strokeWidth="1.7" />
          <path d="M12 4h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-7Z" fill="currentColor" opacity=".2" />
        </>
      ) : (
        <>
          <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.7" />
          <path
            d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </>
      )}
    </svg>
  );
}

export function AppearanceSettings() {
  const { t } = useI18n();
  const { appearance, resolvedMode, updateAppearance, canPersist } = useAppearance();
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
        <ModeIcon mode={resolvedMode} />
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
            <p className="eyebrow">{t('A TABLE OF YOUR OWN')}</p>
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
        <p className="appearance-intro">
          {t(
            'Choose the light, the pieces and the battlefield. Your games stay just as you left them.',
          )}
        </p>
        <fieldset className="appearance-section">
          <legend>{t('Color mode')}</legend>
          <div className="appearance-modes">
            {COLOR_MODES.map((mode) => (
              <label
                className={`appearance-mode ${appearance.mode === mode.id ? 'chosen' : ''}`}
                key={mode.id}
              >
                <input
                  type="radio"
                  name="color-mode"
                  value={mode.id}
                  checked={appearance.mode === mode.id}
                  onChange={() => updateAppearance({ mode: mode.id })}
                  aria-label={t(mode.name)}
                />
                <ModeIcon mode={mode.id} />
                <strong>{t(mode.name)}</strong>
                <small>{t(mode.description)}</small>
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
                  name="token-theme"
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
                  name="board-theme"
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
        <div className="appearance-footer">
          <p role="status">
            {canPersist
              ? t('Your choices are saved on this device.')
              : t('Your browser could not save these choices. They will apply for this visit.')}
          </p>
          <button className="button primary" type="button" onClick={() => setOpen(false)}>
            {t('Back to the table')}
          </button>
        </div>
      </dialog>
    </>
  );
}
