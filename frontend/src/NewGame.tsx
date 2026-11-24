import { useI18n } from './i18n';
import Modal from './Modal';
import { deferredFeature } from './deferredFeature';
import type { NewGameProps } from './NewGameForm';

/**
 * The eager New Game dialog shell.
 *
 * The shell, backdrop, close button, Escape handling and focus management stay
 * in the initial graph so the dialog behaves correctly while the heavy form
 * (opponents, model selection and catalogue controls) is still loading. Only
 * the form itself is deferred, and cancellation unmounts it before a late
 * import can commit.
 */
const NewGameForm = deferredFeature(() => import('./NewGameForm'));

export default function NewGame(props: NewGameProps) {
  const { t } = useI18n();
  return (
    <Modal title={t('Begin a new saga')} onClose={props.onClose}>
      <NewGameForm {...props} />
    </Modal>
  );
}
