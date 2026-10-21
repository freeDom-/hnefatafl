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

interface AppearanceContext {
  appearance: Appearance;
  resolvedMode: 'light' | 'dark';
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
    root.style.colorScheme = resolvedMode;
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
      {children}
    </AppearanceContext.Provider>
  );
}

function ModeIcon({ mode }: { mode: ColorMode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {mode === 'dark' ? (
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
        aria-label="Appearance settings"
      >
        <ModeIcon mode={resolvedMode} />
        <span>Appearance</span>
      </button>
      <dialog
        className="modal appearance-modal"
        ref={dialog}
        aria-label="Appearance settings"
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
            <p className="eyebrow">A TABLE OF YOUR OWN</p>
            <h2>Make the table yours.</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="Close appearance settings"
            onClick={() => setOpen(false)}
          >
            ×
          </button>
        </div>
        <p className="appearance-intro">
          Choose the light, the pieces and the battlefield. Your games stay just as you left them.
        </p>
        <fieldset className="appearance-section">
          <legend>Color mode</legend>
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
                  aria-label={mode.name}
                />
                <ModeIcon mode={mode.id} />
                <strong>{mode.name}</strong>
                <small>{mode.description}</small>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="appearance-section">
          <legend>Token theme</legend>
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
                  aria-label={theme.name}
                />
                <span className="token-preview">
                  {['A', 'D', 'K'].map((piece) => (
                    <Token key={piece} piece={piece} theme={theme.id} />
                  ))}
                </span>
                <strong>{theme.name}</strong>
                <small>{theme.description}</small>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="appearance-section">
          <legend>Board theme</legend>
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
                  aria-label={theme.name}
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
                <strong>{theme.name}</strong>
                <small>{theme.description}</small>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="appearance-footer">
          <p role="status">
            {canPersist
              ? 'Your choices are saved on this device.'
              : 'Your browser could not save these choices. They will apply for this visit.'}
          </p>
          <button className="button primary" type="button" onClick={() => setOpen(false)}>
            Back to the table
          </button>
        </div>
      </dialog>
    </>
  );
}
