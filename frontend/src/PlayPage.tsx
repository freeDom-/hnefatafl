import PageHeading from './PageHeading';
import type { ReactNode } from 'react';

export default function PlayPage({
  className,
  heading,
  children,
  label,
  gameId,
}: {
  className: 'play-page' | 'online-page';
  heading: string;
  children: ReactNode;
  label?: string;
  gameId?: string;
}) {
  return (
    <section className={className} aria-label={label} data-game-id={gameId}>
      <PageHeading title={heading} />
      {children}
    </section>
  );
}
