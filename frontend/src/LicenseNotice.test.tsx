import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import LicenseNotice from './LicenseNotice';
import { setLanguage } from './i18n';

afterEach(() => {
  cleanup();
  setLanguage('en', false);
});

it('names the project license and exposes local terms, notices and a commercial contact', () => {
  render(<LicenseNotice />);
  expect(
    screen.getByText('Source-available under the PolyForm Noncommercial License 1.0.0'),
  ).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'License terms' })).toHaveAttribute(
    'href',
    '/LICENSE.txt',
  );
  expect(screen.getByRole('link', { name: 'Third-party notices' })).toHaveAttribute(
    'href',
    '/THIRD_PARTY_NOTICES.txt',
  );
  expect(screen.getByRole('link', { name: 'Commercial licensing' })).toHaveAttribute(
    'href',
    'https://github.com/freeDom-/hnefatafl/issues',
  );
  const support = screen.getByRole('link', {
    name: 'Support development on Buy Me a Coffee (opens in a new tab)',
  });
  expect(support).toHaveAttribute('href', 'https://buymeacoffee.com/free.dom');
  expect(support).toHaveAttribute('target', '_blank');
  expect(support).toHaveAttribute('rel', 'noopener noreferrer');
});

it('uses the existing German translation mechanism', () => {
  setLanguage('de', false);
  render(<LicenseNotice />);
  expect(
    screen.getByText('Quelltext verfügbar unter der PolyForm Noncommercial License 1.0.0'),
  ).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Lizenzbedingungen' })).toHaveAttribute(
    'href',
    '/LICENSE.txt',
  );
  expect(
    screen.getByRole('link', {
      name: 'Die Entwicklung auf Buy Me a Coffee unterstützen (öffnet in einem neuen Tab)',
    }),
  ).toHaveAttribute('href', 'https://buymeacoffee.com/free.dom');
});
