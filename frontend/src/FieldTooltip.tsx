import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FocusEvent,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { t } from './i18n';
import { trainingTooltip } from './trainingTooltips';

/**
 * Accessible, shared help affordance for the training configuration.
 *
 * A small information button next to a parameter's label reveals the parameter's
 * tooltip. The label and the button share one hover group, so the tooltip opens
 * while the pointer is over either of them and does not flicker when the pointer
 * moves between them. The tooltip is portaled into that same hover group, which
 * keeps it a `position: fixed` descendant of the surrounding native dialog so it
 * is never clipped, and it is positioned from the button's viewport rectangle so
 * it stays inside the visual viewport. A description taller than the visual
 * viewport becomes a focusable, scrollable region so the whole text is reachable.
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
  trigger: {
    onFocus: () => void;
    onBlur: (event: FocusEvent<HTMLButtonElement>) => void;
    onClick: () => void;
  };
  /**
   * Focus handlers for the scrollable oversized panel. They keep the help open
   * while focus moves between the button and the panel, and close it when focus
   * leaves both.
   */
  panel: {
    onFocus: () => void;
    onBlur: (event: FocusEvent<HTMLDivElement>) => void;
  };
  /** The information button; the hook owns it so Escape can return focus. */
  buttonRef: RefObject<HTMLButtonElement | null>;
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
  const buttonRef = useRef<HTMLButtonElement>(null);
  // Set while Escape moves focus back to the button, so the programmatic focus
  // does not immediately reopen the help the user just dismissed.
  const suppressFocus = useRef(false);
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
      // A scrollable panel takes focus, so return it to the button instead of
      // leaving the user on the document when the panel unmounts.
      const focusInPanel =
        document.activeElement !== buttonRef.current &&
        Boolean(container.current?.contains(document.activeElement));
      setState(dismiss);
      if (focusInPanel && buttonRef.current) {
        suppressFocus.current = true;
        buttonRef.current.focus();
      }
    };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', escape, true);
    return () => {
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('keydown', escape, true);
    };
  }, [shown, container]);
  // `relatedTarget` is the element gaining focus. When it is still inside the
  // help group (the button or the scrollable panel), the help stays open.
  const focusStaysInside = (event: FocusEvent<HTMLElement>) =>
    Boolean(event.relatedTarget && container.current?.contains(event.relatedTarget as Node));
  const clearFocus = (previous: Visibility): Visibility =>
    previous.focused || previous.pinned ? { ...previous, focused: false, pinned: false } : previous;
  return {
    shown,
    buttonRef,
    hover: {
      onMouseEnter: () =>
        setState((previous) => ({ ...previous, hovering: true, dismissed: false })),
      onMouseLeave: () =>
        setState((previous) => (previous.hovering ? { ...previous, hovering: false } : previous)),
    },
    trigger: {
      onFocus: () => {
        if (suppressFocus.current) {
          suppressFocus.current = false;
          return;
        }
        setState((previous) => ({ ...previous, focused: true, dismissed: false }));
      },
      onBlur: (event) => {
        if (focusStaysInside(event)) return;
        setState(clearFocus);
      },
      onClick: () =>
        setState((previous) =>
          previous.pinned
            ? { ...previous, pinned: false, dismissed: true }
            : { ...previous, pinned: true, dismissed: false },
        ),
    },
    panel: {
      onFocus: () => setState((previous) => ({ ...previous, focused: true, dismissed: false })),
      onBlur: (event) => {
        if (focusStaysInside(event)) return;
        setState(clearFocus);
      },
    },
  };
}

/** The tooltip itself, mounted only while its trigger is open. */
function FieldTooltipBox({
  anchor,
  id,
  text,
  fieldLabel,
  panel,
}: {
  anchor: RefObject<HTMLElement | null>;
  id: string;
  text: string;
  fieldLabel: string;
  panel: FieldTooltipState['panel'];
}) {
  const box = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  // The last values actually pushed into state, so a scroll or resize that lands
  // on the same coordinates or the same presentation does not schedule a no-op
  // React update at all.
  const appliedPosition = useRef<{ top: number; left: number } | null>(null);
  const appliedOversized = useRef(false);
  // A description taller than the visual viewport becomes a focusable, scrollable
  // region instead of a non-interactive tooltip, so the whole text is reachable.
  const [oversized, setOversized] = useState(false);
  useLayoutEffect(() => {
    const place = () => {
      const trigger = anchor.current;
      const element = box.current;
      if (!trigger || !element) return;
      // `100vw` follows the layout viewport, but a pinch zoom creates a smaller
      // visual viewport. Apply the visible width and height to the border box
      // (the shared `box-sizing: border-box` rule includes padding and border)
      // *before* measuring, so the measured height reflects the wrapped text and
      // the horizontal clamp uses the real width.
      const visual = window.visualViewport;
      const viewWidth = visual?.width ?? window.innerWidth;
      const viewHeight = visual?.height ?? window.innerHeight;
      const maxWidth = Math.max(0, Math.min(MAX_WIDTH, viewWidth - 2 * MARGIN));
      const maxHeight = Math.max(0, viewHeight - 2 * MARGIN);
      const maxWidthStyle = `${maxWidth}px`;
      const maxHeightStyle = `${maxHeight}px`;
      if (element.style.maxWidth !== maxWidthStyle) element.style.maxWidth = maxWidthStyle;
      if (element.style.maxHeight !== maxHeightStyle) element.style.maxHeight = maxHeightStyle;
      const rect = trigger.getBoundingClientRect();
      const size = element.getBoundingClientRect();
      // `scrollHeight` is the full text; `clientHeight` is what the max-height
      // lets through. Only the overflow becomes an interactive scroll panel.
      const nextOversized = element.scrollHeight > element.clientHeight + 1;
      if (appliedOversized.current !== nextOversized) {
        appliedOversized.current = nextOversized;
        setOversized(nextOversized);
      }
      // A fixed tooltip is laid out against the layout viewport, but the visual
      // viewport can be both smaller and displaced from it (a mobile keyboard, or
      // a pinch zoom), and its rectangle is what is actually readable. Both
      // getBoundingClientRect and the fixed coordinates are in layout-viewport
      // space, so the offset is added rather than ignored.
      const viewLeft = visual?.offsetLeft ?? 0;
      const viewTop = visual?.offsetTop ?? 0;
      const viewRight = viewLeft + viewWidth;
      const viewBottom = viewTop + viewHeight;
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
      // (a description taller than the viewport starts at the top edge and
      // scrolls inside its max-height).
      const wanted =
        size.height <= roomBelow
          ? below
          : size.height <= roomAbove
            ? above
            : roomBelow >= roomAbove
              ? below
              : above;
      const top = Math.max(viewTop + MARGIN, Math.min(wanted, viewBottom - size.height - MARGIN));
      // A resize or scroll that lands on the same coordinates must not schedule a
      // React update or force an extra render.
      if (appliedPosition.current?.top !== top || appliedPosition.current?.left !== left) {
        appliedPosition.current = { top, left };
        setPosition({ top, left });
      }
    };
    // The panel is itself scrollable, so a capture-phase scroll from inside it
    // must not reposition the panel. Scrolls on the page or a containing dialog
    // still move the trigger and need a fresh placement.
    const onScroll = (event: Event) => {
      if (event.target instanceof Node && box.current?.contains(event.target)) return;
      place();
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', onScroll, true);
    window.visualViewport?.addEventListener('resize', place);
    window.visualViewport?.addEventListener('scroll', place);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', onScroll, true);
      window.visualViewport?.removeEventListener('resize', place);
      window.visualViewport?.removeEventListener('scroll', place);
    };
  }, [anchor, text]);
  // The tooltip joins the element that owns its hover and outside-click checks
  // (the `.field-help` group or `.checkbox-field` row). It stays a
  // `position: fixed` descendant of the surrounding native dialog, so a modal
  // never clips it and it paints in the dialog's top layer, while the DOM
  // nesting lets the shared handlers treat clicks and focus on the scroll panel
  // as "inside".
  const layer = (anchor.current?.parentElement as HTMLElement | null) ?? document.body;
  return createPortal(
    <div
      ref={box}
      role={oversized ? 'region' : 'tooltip'}
      aria-label={oversized ? fieldLabel : undefined}
      tabIndex={oversized ? 0 : undefined}
      id={id}
      className={oversized ? 'field-tooltip field-tooltip-scroll' : 'field-tooltip'}
      style={
        position
          ? { top: position.top, left: position.left }
          : { top: 0, left: 0, visibility: 'hidden' }
      }
      {...panel}
    >
      {/* The button describes this inner text rather than the panel itself: when
          the panel is a named region, referencing the panel would make the
          description its name instead of the full description. */}
      <span id={`${id}-text`}>{text}</span>
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
  id,
  text,
  fieldLabel,
  state,
}: {
  id: string;
  text: string;
  fieldLabel: string;
  state: FieldTooltipState;
}) {
  return (
    <>
      <button
        type="button"
        ref={state.buttonRef}
        className="field-info-button"
        aria-label={t('Explain {field}', { field: fieldLabel })}
        aria-describedby={state.shown ? `${id}-text` : undefined}
        {...state.trigger}
      >
        <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">
          <path
            fill="currentColor"
            d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm0 2.7a1.15 1.15 0 1 1 0 2.3 1.15 1.15 0 0 1 0-2.3Zm1.35 9.1H6.6v-1.1h.85V7.85H6.6v-1.1h2.05v5.05h.7Z"
          />
        </svg>
      </button>
      {state.shown && (
        <FieldTooltipBox
          anchor={state.buttonRef}
          id={id}
          text={text}
          fieldLabel={fieldLabel}
          panel={state.panel}
        />
      )}
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
  tooltip,
}: {
  htmlFor: string;
  label: string;
  tooltipKey?: string;
  tooltip?: string;
}) {
  const group = useRef<HTMLDivElement>(null);
  const state = useFieldTooltip(group);
  const text = tooltip ?? (tooltipKey ? trainingTooltip(tooltipKey) : undefined);
  return (
    <div className="field-help" ref={group} {...state.hover}>
      <label htmlFor={htmlFor}>{t(label)}</label>
      {text && (
        <FieldTooltipTrigger
          id={`${htmlFor}-help`}
          text={t(text)}
          fieldLabel={t(label)}
          state={state}
        />
      )}
    </div>
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
  const group = useRef<HTMLDivElement>(null);
  const state = useFieldTooltip(group);
  const id = useId();
  return (
    <div className="field-help" data-training-parameter={tooltipKey} ref={group} {...state.hover}>
      <span>{t(label)}</span>
      <FieldInfo tooltipKey={tooltipKey} fieldLabel={t(label)} state={state} id={`${id}-help`} />
    </div>
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
  const text = trainingTooltip(tooltipKey);
  if (!text) return null;
  return <FieldTooltipTrigger id={id} text={t(text)} fieldLabel={fieldLabel} state={state} />;
}
