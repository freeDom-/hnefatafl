import type { Job } from './types';

export const jobTabs = ['Overview', 'Learning', 'Evaluation', 'Performance', 'Details'] as const;
export type JobTab = (typeof jobTabs)[number];
export const defaultJobTab: JobTab = 'Overview';

export function isJobTab(value: unknown): value is JobTab {
  return typeof value === 'string' && (jobTabs as readonly string[]).includes(value);
}

/**
 * Analysis tabs a job can render. Every current job kind and status supports the
 * same views, so all tabs stay available; this stays a function so a future job
 * variant can narrow the set in one place.
 */
export function availableJobTabs(_job: Job): readonly JobTab[] {
  return jobTabs;
}

/**
 * Keep a preserved tab when the job supports it and fall back to the default tab
 * otherwise. Unknown or unavailable values never leave the tablist and tabpanel
 * out of sync.
 */
export function resolveJobTab(tab: unknown, available: readonly JobTab[] = jobTabs): JobTab {
  return isJobTab(tab) && available.includes(tab) ? tab : defaultJobTab;
}
