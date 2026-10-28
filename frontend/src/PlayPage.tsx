import type { ReactNode } from 'react';

export default function PlayPage({
  className,
  heading,
  children,
}: {
  className: 'play-page' | 'online-page';
  heading: string;
  children: ReactNode;
}) {
  return (
    <section className={className}>
      <h1 className="sr-only" tabIndex={-1}>
        {heading}
      </h1>
      {children}
    </section>
  );
}
