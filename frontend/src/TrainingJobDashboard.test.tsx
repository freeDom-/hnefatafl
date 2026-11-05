import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TrainingJobDashboard from './TrainingJobDashboard';
import { setLanguage } from './i18n';
import type { Job } from './types';

function completedJob(result: Record<string, unknown>): Job {
  return {
    id: 'reuse-job',
    kind: 'train',
    status: 'completed',
    config: { model_name: 'Reused raven', ruleset: 'brandubh', iterations: 1 },
    progress: {},
    result,
  };
}

function show(result: Record<string, unknown>) {
  render(
    <TrainingJobDashboard
      job={completedJob(result)}
      tab="Overview"
      onTabChange={vi.fn()}
      revision={0}
      busy={false}
      exporting={false}
      onCancel={vi.fn()}
      onResume={vi.fn()}
      onExport={vi.fn()}
      onRefresh={vi.fn()}
      onDelete={vi.fn()}
      onRename={vi.fn()}
      onUseParameters={vi.fn()}
      onTuneCheckpoint={vi.fn()}
      canUseParameters={false}
      trainingJobs={[]}
      onUseBestParameters={vi.fn()}
      onContinueTrial={vi.fn()}
      onApplyToJob={vi.fn()}
      onDownloadBestConfig={vi.fn()}
    />,
  );
}

afterEach(() => {
  cleanup();
  setLanguage('en', false);
});

describe('reused-model outcome message', () => {
  it('reports a produced best whose weights already matched a registered model', () => {
    show({ model_reused: true, produced_best: true });
    expect(
      screen.getByText('The trained weights already matched a registered model, which was reused.'),
    ).toBeInTheDocument();
  });

  it('reports that no new best model was produced', () => {
    show({ model_reused: true, produced_best: false });
    expect(
      screen.getByText('No new best model was produced. The existing registered model was reused.'),
    ).toBeInTheDocument();
  });

  it('shows a neutral message when improvement status is unknown', () => {
    show({ model_reused: true });
    expect(screen.getByText('The existing registered model was reused.')).toBeInTheDocument();
    expect(
      screen.queryByText(
        'No new best model was produced. The existing registered model was reused.',
      ),
    ).not.toBeInTheDocument();
  });

  it('shows a legacy completed job result with unknown status neutrally', () => {
    // Older jobs predate produced_best entirely.
    show({ model: { id: 'legacy-model' }, model_reused: true });
    expect(screen.getByText('The existing registered model was reused.')).toBeInTheDocument();
  });

  it('stays silent when the run registered a new model', () => {
    show({ model_reused: false, produced_best: true });
    expect(screen.queryByText('The existing registered model was reused.')).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        'The trained weights already matched a registered model, which was reused.',
      ),
    ).not.toBeInTheDocument();
  });

  it('translates the neutral message in German', () => {
    setLanguage('de', false);
    show({ model_reused: true });
    expect(
      screen.getByText('Das vorhandene registrierte Modell wurde wiederverwendet.'),
    ).toBeInTheDocument();
  });
});
