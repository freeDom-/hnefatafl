import { describe, expect, it } from 'vitest';
import { availableJobTabs, defaultJobTab, isJobTab, jobTabs, resolveJobTab } from './jobTabs';
import type { Job } from './types';

const job = (overrides: Partial<Job> = {}): Job => ({
  id: 'job',
  status: 'running',
  ...overrides,
});

describe('job tabs', () => {
  it('keeps a known tab that the job supports', () => {
    expect(resolveJobTab('Evaluation', availableJobTabs(job()))).toBe('Evaluation');
    expect(resolveJobTab('Details', availableJobTabs(job()))).toBe('Details');
  });

  it('falls back to the default tab for unknown or unavailable tabs', () => {
    expect(defaultJobTab).toBe('Overview');
    expect(resolveJobTab('Not a tab', availableJobTabs(job()))).toBe(defaultJobTab);
    expect(resolveJobTab(undefined, availableJobTabs(job()))).toBe(defaultJobTab);
    expect(resolveJobTab('Performance', ['Overview', 'Details'])).toBe(defaultJobTab);
    expect(resolveJobTab('Learning', [])).toBe(defaultJobTab);
  });

  it('exposes every analysis tab for every job kind and status', () => {
    for (const kind of ['train', 'tune']) {
      for (const status of ['running', 'queued', 'completed', 'failed', 'cancelled']) {
        expect(availableJobTabs(job({ kind, status }))).toEqual(jobTabs);
      }
    }
    expect(isJobTab('Learning')).toBe(true);
    expect(isJobTab('learning')).toBe(false);
    expect(isJobTab(3)).toBe(false);
  });
});
