import { useState, type ReactNode } from 'react';
import { useI18n } from './i18n';
/** Mount large tables only on demand; native disclosure retains keyboard behavior. */
export default function TrainingDataDisclosure({
  title,
  children,
}: {
  title: string;
  children: () => ReactNode;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{t(title)}</summary>
      {open && children()}
    </details>
  );
}
