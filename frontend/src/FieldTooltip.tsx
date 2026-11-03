import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { t } from './i18n';
import { trainingTooltip } from './trainingTooltips';

/**
 * Accessible, shared help affordance for the training configuration.
 *
 * A small information button next to a parameter's label reveals the parameter's
 * tooltip. The label and the button share one hover group, so the tooltip opens
 * while the pointer is over either of them and does not flicker when the pointer
 * moves between them. The tooltip is portaled into the surrounding native dialog
 * (falling back to the document body) so it is never clipped by a dialog or
 * scroll container, and it is positioned from the button's viewport rectangle so
 * it stays inside the viewport.
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
/** Normal maximum width before the visual viewport constrains it. */
const MAX_WIDTH = 300;

interface Visibility {
  /** The pointer is over the label or the information button. */
  hovering: boolean;
  /** The information button has DOM focus. */
  focused: boolean;
  /** The user opened it explicitly, so it outlives the pointer leaving. */
  pinned: boolean;
  /** The user closed it explicitly; wins until the next hover or focus. */
  dismissed: boolean;
}

const IDLE: Visibility = { hovering: false, focused: false, pinned: false, dismissed: false };

export interface FieldTooltipState {
  shown: boolean;
  /** Hover handlers for the group that holds the label and the button. */
  hover: { onMouseEnter: () => void; onMouseLeave: () => void };
  /** Handlers for the information button. */
  trigger: { onFocus: () => void; onBlur: () => void; onClick: () => void };
}

/**
 * Open/close state for one help affordance.
 *
 * Hover and focus open the tooltip; a click or tap pins it so it survives the
 * pointer leaving, and activating again closes it even though the button keeps
 * DOM focus. `dismissed` records that explicit close so a still-hovered or still
 * focused button does not immediately show the tooltip again, and it is cleared
 * by the next hover or focus so the affordance always works a second time.
 */
export function useFieldTooltip(container: RefObject<HTMLElement | null>): FieldTooltipState {
  const [state, setState] = useState<Visibility>(IDLE);
  const shown = !state.dismissed && (state.hovering || state.focused || state.pinned);
  useEffect(() => {
    if (!shown) return;
    const dismiss = (previous: Visibility): Visibility => ({
      ...previous,
      hovering: false,
      pinned: false,
      dismissed: true,
    });
    const outside = (event: Event) => {
      if (container.current?.contains(event.target as Node)) return;
      // A press elsewhere closes the tooltip without moving focus.
      setState(dismiss);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Dismiss the tooltip first; the surrounding dialog stays open. Once it is
      // closed this listener is gone, so Escape reaches the dialog again.
      event.preventDefault();
      setState(dismiss);
    };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', escape, true);
    return () => {
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('keydown', escape, true);
    };
  }, [shown, container]);
  return {
    shown,
    hover: {
      onMouseEnter: () =>
        setState((previous) => ({ ...previous, hovering: true, dismissed: false })),
      onMouseLeave: () =>
        setState((previous) => (previous.hovering ? { ...previous, hovering: false } : previous)),
    },
    trigger: {
      onFocus: () => setState((previous) => ({ ...previous, focused: true, dismissed: false })),
      onBlur: () =>
        setState((previous) =>
          previous.focused || previous.pinned
            ? { ...previous, focused: false, pinned: false }
            : previous,
        ),
      onClick: () =>
        setState((previous) =>
          previous.pinned
            ? { ...previous, pinned: false, dismissed: true }
            : { ...previous, pinned: true, dismissed: false },
        ),
    },
  };
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
      // `100vw` follows the layout viewport, but a pinch zoom creates a smaller
      // visual viewport. Apply the visible width to the border box (the shared
      // `box-sizing: border-box` rule includes padding and border) *before*
      // measuring, so the measured height reflects the wrapped text and the
      // horizontal clamp uses the real width.
      const visual = window.visualViewport;
      const viewWidth = visual?.width ?? window.innerWidth;
      const maxWidth = Math.max(0, Math.min(MAX_WIDTH, viewWidth - 2 * MARGIN));
      const maxWidthStyle = `${maxWidth}px`;
      if (element.style.maxWidth !== maxWidthStyle) element.style.maxWidth = maxWidthStyle;
      const rect = trigger.getBoundingClientRect();
      const size = element.getBoundingClientRect();
      // A fixed tooltip is laid out against the layout viewport, but the visual
      // viewport can be both smaller and displaced from it (a mobile keyboard, or
      // a pinch zoom), and its rectangle is what is actually readable. Both
      // getBoundingClientRect and the fixed coordinates are in layout-viewport
      // space, so the offset is added rather than ignored.
      const viewLeft = visual?.offsetLeft ?? 0;
      const viewTop = visual?.offsetTop ?? 0;
      const viewRight = viewLeft + viewWidth;
      const viewBottom = viewTop + (visual?.height ?? window.innerHeight);
      const left = Math.max(
        viewLeft + MARGIN,
        Math.min(rect.left + rect.width / 2 - size.width / 2, viewRight - size.width - MARGIN),
      );
      const roomBelow = viewBottom - rect.bottom - GAP - MARGIN;
      const roomAbove = rect.top - viewTop - GAP - MARGIN;
      const below = rect.bottom + GAP;
      const above = rect.top - size.height - GAP;
      // Prefer a side that fits the whole tooltip, then the roomier side, and
      // finally clamp so a tooltip that fits anywhere is never partly off screen
      // (a description taller than the viewport starts at the top edge).
      const wanted =
        size.height <= roomBelow
          ? below
          : size.height <= roomAbove
            ? above
            : roomBelow >= roomAbove
              ? below
              : above;
      setPosition({
        top: Math.max(viewTop + MARGIN, Math.min(wanted, viewBottom - size.height - MARGIN)),
        left,
      });
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    window.visualViewport?.addEventListener('resize', place);
    window.visualViewport?.addEventListener('scroll', place);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      window.visualViewport?.removeEventListener('resize', place);
      window.visualViewport?.removeEventListener('scroll', place);
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
        {...state.trigger}
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
 * The label and the button share one hover group, which is also the container
 * that decides what counts as a press outside.
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
  const group = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const state = useFieldTooltip(group);
  const text = tooltipKey ? trainingTooltip(tooltipKey) : undefined;
  return (
    <span className="field-help" ref={group} {...state.hover}>
      <label htmlFor={htmlFor}>{t(label)}</label>
      {text && (
        <FieldTooltipTrigger
          buttonRef={button}
          id={`${htmlFor}-help`}
          text={t(text)}
          fieldLabel={t(label)}
          state={state}
        />
      )}
    </span>
  );
}

/**
 * A read-only label with its parameter's tooltip.
 *
 * The resume checkpoint summary shows immutable values that have no form
 * control but must explain themselves exactly like the editable fields. This is
 * the `FieldLabel` affordance without a `<label>`: the visible text and the
 * information button share one hover group, the button names the parameter it
 * belongs to, and the surrounding structure (for example `dt`/`dd`) is left to
 * the caller. The `data-training-parameter` marker lets the coverage test
 * enumerate read-only parameters the way `data-training-field` does for
 * editable controls.
 */
export function ReadOnlyFieldLabel({ label, tooltipKey }: { label: string; tooltipKey: string }) {
  const group = useRef<HTMLSpanElement>(null);
  const state = useFieldTooltip(group);
  const id = useId();
  return (
    <span className="field-help" data-training-parameter={tooltipKey} ref={group} {...state.hover}>
      <span>{t(label)}</span>
      <FieldInfo tooltipKey={tooltipKey} fieldLabel={t(label)} state={state} id={`${id}-help`} />
    </span>
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
