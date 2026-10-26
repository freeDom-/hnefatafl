import type { ReactNode } from 'react';
import { surfaceLink } from './surfaceNavigation';

export default function HeaderLink({
  href,
  children,
  current = false,
}: {
  href: string;
  children: ReactNode;
  current?: boolean;
}) {
  return (
    <a
      className="header-link button small secondary"
      href={href}
      onClick={surfaceLink}
      aria-current={current ? 'page' : undefined}
    >
      {children}
    </a>
  );
}
