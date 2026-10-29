import type { ReactNode } from 'react';

/** Keep page names and descriptions available without repeating navigation chrome. */
export default function PageHeading({
  title,
  subtitle,
  id,
}: {
  title: string;
  subtitle?: ReactNode;
  id?: string;
}) {
  return (
    <div className="page-heading sr-only">
      <h1 id={id} className="page-title" tabIndex={-1}>
        {title}
      </h1>
      {subtitle && <p>{subtitle}</p>}
    </div>
  );
}
