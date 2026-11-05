import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import TuningOpponents from './TuningOpponents';
import { setLanguage } from './i18n';
import { trainingTooltip } from './trainingTooltips';

afterEach(() => {
  cleanup();
  setLanguage('en', false);
});

it.each(['en', 'de'] as const)(
  'offers an accessible weight tooltip even with disabled controls (%s)',
  (language) => {
    setLanguage(language, false);
    render(
      <TuningOpponents
        models={[]}
        ruleset="brandubh"
        strategy={'{"opponents":[{"opponent":"jarl","weight":0.75}]}'}
        onChange={() => {}}
        disabled
      />,
    );
    const label = language === 'en' ? 'Opponent weight 1' : 'Gegnergewicht 1';
    expect(screen.getByLabelText(label)).toBeDisabled();
    const help = screen.getByRole('button', { name: new RegExp(label) });
    expect(help).toBeEnabled();
    fireEvent.focus(help);
    expect(screen.getByRole('tooltip')).toBeVisible();
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      language === 'en'
        ? trainingTooltip('opponent_weight')!
        : 'Gewichte werden durch ihre Summe normalisiert',
    );
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      language === 'en'
        ? 'They do not change evaluation game counts'
        : 'Sie ändern nicht die Anzahl der Auswertungspartien',
    );
    fireEvent.keyDown(help, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
  },
);
