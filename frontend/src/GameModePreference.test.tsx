import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewGame } from './App';
import { GAME_MODE_KEY, readLastGameMode } from './gameModePreference';
import type { AIProfile, Game, Ruleset } from './types';

const rulesets: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const profiles: AIProfile[] = [
  {
    id: 'baseline',
    name: 'Shield bearer',
    algorithm: 'baseline',
    difficulty: 'Beginner',
    parameters: { time_limit: 1 },
    supported_rulesets: ['brandubh'],
  },
];
const created = { id: 'created-game', mode: 'ai-ai' } as Game;
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);

beforeEach(() => {
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function show(onCreated = vi.fn(), onClose = vi.fn()) {
  return render(
    <NewGame
      rulesets={rulesets}
      profiles={profiles}
      models={[]}
      players={[]}
      initialRuleset="brandubh"
      onClose={onClose}
      onCreated={onCreated}
    />,
  );
}

describe('last successfully played game mode', () => {
  it('restores the last successful choice after remount and does not save a cancelled edit', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json(created)),
    );
    const onCreated = vi.fn();
    const first = show(onCreated);
    expect(screen.getByRole('button', { name: 'Pass & play' })).toHaveClass('active');
    fireEvent.click(screen.getByRole('button', { name: 'AI vs AI' }));
    expect(localStorage.getItem(GAME_MODE_KEY)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
    expect(localStorage.getItem(GAME_MODE_KEY)).toBe('ai-ai');
    first.unmount();

    const onClose = vi.fn();
    const second = show(vi.fn(), onClose);
    expect(screen.getByRole('button', { name: 'AI vs AI' })).toHaveClass('active');
    fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
    expect(screen.getByRole('button', { name: 'Human vs AI' })).toHaveClass('active');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(localStorage.getItem(GAME_MODE_KEY)).toBe('ai-ai');
    second.unmount();
    show();
    expect(screen.getByRole('button', { name: 'AI vs AI' })).toHaveClass('active');
  });

  it('keeps the last played mode when creating another mode fails', async () => {
    localStorage.setItem(GAME_MODE_KEY, 'human-ai');
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json({ detail: 'The game could not be created.' }, false)),
    );
    const onCreated = vi.fn();
    show(onCreated);
    expect(screen.getByRole('button', { name: 'Human vs AI' })).toHaveClass('active');
    fireEvent.click(screen.getByRole('button', { name: 'AI vs AI' }));
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await screen.findByText('The game could not be created.');
    expect(onCreated).not.toHaveBeenCalled();
    expect(localStorage.getItem(GAME_MODE_KEY)).toBe('human-ai');
  });

  it('uses pass-and-play for malformed preferences and blocked reads', () => {
    for (const invalid of ['tournament', 'HUMAN-AI', '{"mode":"ai-ai"}', '"ai-ai"', '']) {
      localStorage.setItem(GAME_MODE_KEY, invalid);
      expect(readLastGameMode()).toBe('human-human');
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Storage unavailable', 'SecurityError');
    });
    show();
    expect(screen.getByRole('button', { name: 'Pass & play' })).toHaveClass('active');
  });

  it('still starts the selected mode when preferences cannot be written', async () => {
    const save = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage full', 'QuotaExceededError');
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json(created)),
    );
    const onCreated = vi.fn();
    show(onCreated);
    fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
    expect(save).toHaveBeenCalledWith(GAME_MODE_KEY, 'human-ai');
    expect(screen.queryByText('Storage full')).not.toBeInTheDocument();
  });
});
