import { useRef } from 'react';
import { useI18n } from './i18n';

export type TabItem = { id: string; label: string };

/**
 * Shared accessible tab list (roving tabindex, arrow/Home/End keys). The jobs
 * view and the online lobby use the same component and visual conventions.
 */
export default function Tabs({
  id,
  items,
  active,
  onChange,
  label,
  className = '',
}: {
  id: string;
  items: readonly TabItem[];
  active: string;
  onChange: (id: string) => void;
  label: string;
  className?: string;
}) {
  const { t } = useI18n();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div className={`tabs ${className}`.trim()} role="tablist" aria-label={t(label)}>
      {items.map((item, index) => (
        <button
          key={item.id}
          ref={(element) => {
            buttons.current[index] = element;
          }}
          id={`${id}-tab-${item.id}`}
          role="tab"
          aria-selected={item.id === active}
          aria-controls={`${id}-panel`}
          tabIndex={item.id === active ? 0 : -1}
          onClick={() => onChange(item.id)}
          onKeyDown={(event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? items.length - 1
                  : (index + (event.key === 'ArrowRight' ? 1 : items.length - 1)) % items.length;
            onChange(items[next].id);
            buttons.current[next]?.focus();
          }}
        >
          {t(item.label)}
        </button>
      ))}
    </div>
  );
}
