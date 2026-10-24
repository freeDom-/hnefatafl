import { expect, type Page } from '@playwright/test';
export async function openCreation(page: Page) {
  const toggle = page.getByRole('button', { name: 'Forge a new strategist', exact: true });
  await expect(toggle).toBeVisible();
  await expect(page.getByText('Loading training jobs…', { exact: true })).toHaveCount(0);
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
}
export async function openCollection(page: Page) {
  const toggle = page.getByRole('button', { name: /Model collection/ });
  await expect(toggle).toBeVisible();
  await expect(page.getByText('Loading training jobs…', { exact: true })).toHaveCount(0);
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
}
export async function selectWorkshopJob(page: Page, id: string, name: string) {
  await expect(page.getByRole('navigation', { name: 'Training jobs', exact: true })).toBeVisible();
  const select = page.getByLabel('Selected job', { exact: true });
  if (await select.isVisible()) await select.selectOption(id);
  else await page.locator(`.job-row[data-job-id="${id}"]`).filter({ hasText: name }).click();
}
