import type { Model } from './types';
import { t } from './i18n';

type Translate = (text: string, values?: Record<string, string | number>) => string;
const english: Translate = (text, values = {}) =>
  text.replace(/\{(\w+)\}/g, (match, key: string) => String(values[key] ?? match));

/** An import's default version alone does not establish completed training. */
export function modelIteration(model: Model): number | undefined {
  if (typeof model.iteration === 'number' && Number.isFinite(model.iteration))
    return model.iteration;
  return typeof model.training_config?.iterations === 'number' &&
    Number.isFinite(model.training_config.iterations) &&
    typeof model.version === 'number' &&
    Number.isFinite(model.version)
    ? model.version
    : undefined;
}

/** Include the saved training iteration and architecture, plus a stable identity. */
export function modelOptionLabel(model: Model, t: Translate = english): string {
  const metadata = model as Model & {
    iteration?: number;
    architecture?: { channels?: number; blocks?: number };
  };
  const iteration = modelIteration(model);
  const total = model.training_config?.iterations;
  const channels = metadata.architecture?.channels ?? model.training_config?.channels;
  const blocks = metadata.architecture?.blocks ?? model.training_config?.blocks;
  const parts = [model.rune ? `${model.rune} ${model.name}` : model.name];
  if (typeof iteration === 'number' && Number.isFinite(iteration)) {
    parts.push(
      typeof total === 'number' && Number.isFinite(total) && total >= iteration
        ? t('iteration {iteration}/{total}', { iteration, total })
        : t('iteration {iteration}', { iteration }),
    );
  } else if (model.version !== undefined && model.version !== '') {
    parts.push(t('version {version}', { version: String(model.version) }));
  }
  if (typeof channels === 'number' && typeof blocks === 'number')
    parts.push(t('{channels} channels · {blocks} blocks', { channels, blocks }));
  parts.push(`#${model.id.slice(0, 8)}`);
  return parts.join(' · ');
}

export const modelLabel = (model: Model) => modelOptionLabel(model, t);
