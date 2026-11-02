import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { t } from './i18n';
import { trainingTooltip } from './trainingTooltips';

/**
 * Accessible, shared help affordance for the training configuration.
 *
 * A small information button next to a parameter's label reveals the parameter's
 * tooltip on hover, keyboard focus or tap. The tooltip is portaled into the
 * surrounding native dialog (falling back to the document body) so it is never
 * clipped by a dialog or scroll container, and it is positioned from the button's
 * viewport rectangle so it stays inside the viewport.
 *
 * Native `title` attributes are deliberately not used: they cannot be styled,
 * translated or reached reliably on touch. Copy goes through the module-level
 * translator, like the rest of the training fields, so a language change
 * re-renders the form and its help text together.
 */

/** Distance the tooltip keeps from the viewport edges. */
const MARGIN = 8;
/** Space between the trigger and the tooltip. */
const GAP = 6;

export interface FieldTooltipState {
  shown: boolean;
  setHovered: (hovered: boolean) => void;
  setFocused: (focused: boolean) => void;
  setPinned: (pinned: boolean | ((previous: boolean) => boolean)) => void;
}

/**
 * Open/close state for one help affordance. Mouse hover, keyboard focus and a
 * tap are tracked separately so a tapped tooltip stays open until it is
 * dismissed, while a hovered one follows the pointer. Escape and a press outside
 * the container close it without touching focus.
 */
export function useFieldTooltip(container: RefObject<HTMLElement | null>): FieldTooltipState {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const shown = hovered || focused || pinned;
  useEffect(() => {
    if (!shown) return;
    const close = () => {
      setHovered(false);
      setFocused(false);
      setPinned(false);
    };
    const outside = (event: Event) => {
      if (!container.current?.contains(event.target as Node)) close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Dismiss the tooltip first; the surrounding dialog stays open.
      event.preventDefault();
      close();
    };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', escape, true);
    return () => {
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('keydown', escape, true);
    };
  }, [shown, container]);
  return { shown, setHovered, setFocused, setPinned };
}

/** The tooltip itself, mounted only while its trigger is open. */
function FieldTooltipBox({
  anchor,
  id,
  text,
}: {
  anchor: RefObject<HTMLElement | null>;
  id: string;
  text: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    const place = () => {
      const trigger = anchor.current;
      const element = box.current;
      if (!trigger || !element) return;
      const rect = trigger.getBoundingClientRect();
      const size = element.getBoundingClientRect();
      const viewportWidth = window.innerWidth || 0;
      const viewportHeight = window.innerHeight || 0;
      const left = Math.max(
        MARGIN,
        Math.min(rect.left + rect.width / 2 - size.width / 2, viewportWidth - size.width - MARGIN),
      );
      const below = rect.bottom + GAP;
      const above = rect.top - size.height - GAP;
      const fitsBelow = below + size.height <= viewportHeight - MARGIN;
      setPosition({ top: fitsBelow || above < MARGIN ? below : above, left });
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [anchor, text]);
  // Native modal dialogs paint above every ordinary fixed layer, so the tooltip
  // joins the dialog its trigger belongs to and otherwise lives in the document
  // body. This mirrors the shared notification viewport.
  const layer = (anchor.current?.closest('dialog[open]') as HTMLElement | null) ?? document.body;
  return createPortal(
    <div
      ref={box}
      role="tooltip"
      id={id}
      className="field-tooltip"
      style={
        position
          ? { top: position.top, left: position.left }
          : { top: 0, left: 0, visibility: 'hidden' }
      }
    >
      {text}
    </div>,
    layer,
  );
}

/**
 * The information button and, while it is open, its tooltip. The button owns the
 * tooltip's `aria-describedby`, so screen readers announce the description while
 * the control keeps its own accessible name.
 */
function FieldTooltipTrigger({
  buttonRef,
  id,
  text,
  fieldLabel,
  state,
}: {
  buttonRef: RefObject<HTMLButtonElement | null>;
  id: string;
  text: string;
  fieldLabel: string;
  state: FieldTooltipState;
}) {
  return (
    <>
      <button
        type="button"
        ref={buttonRef}
        className="field-info-button"
        aria-label={t('Explain {field}', { field: fieldLabel })}
        aria-describedby={state.shown ? id : undefined}
        onFocus={() => state.setFocused(true)}
        onBlur={() => {
          state.setFocused(false);
          state.setPinned(false);
        }}
        onClick={(event) => {
          // Keyboard activation reports detail 0; focus already reveals it.
          if (event.detail > 0) state.setPinned((previous) => !previous);
        }}
      >
        <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">
          <path
            fill="currentColor"
            d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm0 2.7a1.15 1.15 0 1 1 0 2.3 1.15 1.15 0 0 1 0-2.3Zm1.35 9.1H6.6v-1.1h.85V7.85H6.6v-1.1h2.05v5.05h.7Z"
          />
        </svg>
      </button>
      {state.shown && <FieldTooltipBox anchor={buttonRef} id={id} text={text} />}
    </>
  );
}

/**
 * A label with its parameter's tooltip. The label uses an explicit `htmlFor`, so
 * the description button never becomes part of the control's accessible name.
 * Hovering the label text reveals the tooltip as well as the button, and the
 * button doubles as the container that decides what counts as an outside press.
 */
export function FieldLabel({
  htmlFor,
  label,
  tooltipKey,
}: {
  htmlFor: string;
  label: string;
  tooltipKey?: string;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const state = useFieldTooltip(button);
  const text = tooltipKey ? trainingTooltip(tooltipKey) : undefined;
  return (
    <>
      <label
        htmlFor={htmlFor}
        onMouseEnter={() => state.setHovered(true)}
        onMouseLeave={() => state.setHovered(false)}
      >
        {t(label)}
      </label>
      {text && (
        <FieldTooltipTrigger
          buttonRef={button}
          id={`${htmlFor}-help`}
          text={t(text)}
          fieldLabel={t(label)}
          state={state}
        />
      )}
    </>
  );
}

/**
 * A standalone help button for markup whose label is not a plain `FieldLabel`
 * (for example a checkbox whose label wraps its input). The caller supplies the
 * hover container so hovering that label reveals the tooltip too.
 */
export function FieldInfo({
  tooltipKey,
  fieldLabel,
  state,
  id,
}: {
  tooltipKey: string;
  fieldLabel: string;
  state: FieldTooltipState;
  id: string;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const text = trainingTooltip(tooltipKey);
  if (!text) return null;
  return (
    <FieldTooltipTrigger
      buttonRef={button}
      id={id}
      text={t(text)}
      fieldLabel={fieldLabel}
      state={state}
    />
  );
}
