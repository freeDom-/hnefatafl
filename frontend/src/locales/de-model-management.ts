const translations: Record<string, string> = {
  'Rename model': 'Modell umbenennen',
  'Rename model {name}': 'Modell {name} umbenennen',
  'Close rename model': 'Umbenennen schließen',
  'The model keeps its identity, rune and weights. Saved games and tournaments retain their recorded names.':
    'Identität, Rune und Gewichte des Modells bleiben erhalten. Gespeicherte Spiele und Turniere behalten ihre aufgezeichneten Namen.',
  'Save name': 'Namen speichern',
  'Model renamed. Its identity and saved history are unchanged.':
    'Modell umbenannt. Identität und gespeicherter Verlauf bleiben erhalten.',
  'The model name could not be saved. Try again.':
    'Der Modellname konnte nicht gespeichert werden. Versuche es erneut.',
  'Model name must contain 1 to 100 characters': 'Der Modellname muss 1 bis 100 Zeichen enthalten',
  'Load training parameters': 'Trainingsparameter laden',
  'Load training parameters for {name}': 'Trainingsparameter für {name} laden',
  'No recorded training parameters are available for this model.':
    'Für dieses Modell sind keine aufgezeichneten Trainingsparameter verfügbar.',
  'Training parameters loaded from {name}. Unrecorded inputs keep their current values.':
    'Trainingsparameter von {name} geladen. Nicht aufgezeichnete Eingaben behalten ihre aktuellen Werte.',
  'Delete training data': 'Trainingsdaten löschen',
  'Delete training data?': 'Trainingsdaten löschen?',
  'Permanently remove the checkpoints, replay buffer, learning history and logs for this training run?':
    'Kontrollpunkte, Trainingspuffer, Lernverlauf und Protokolle dieses Trainingslaufs dauerhaft entfernen?',
  'This run can no longer be resumed. Registered models, their recorded training parameters, saved games and tournaments are kept.':
    'Dieser Lauf kann danach nicht fortgesetzt werden. Registrierte Modelle, ihre aufgezeichneten Trainingsparameter, gespeicherte Spiele und Turniere bleiben erhalten.',
  'A study shared by other saved jobs must be kept until those jobs are removed.':
    'Eine von anderen gespeicherten Aufträgen gemeinsam genutzte Studie muss erhalten bleiben, bis diese Aufträge entfernt wurden.',
  'Training data deleted. Registered models and saved game history have been kept.':
    'Trainingsdaten gelöscht. Registrierte Modelle und gespeicherte Spielverläufe bleiben erhalten.',
  'Stop this training job before deleting its data.':
    'Beende diesen Trainingsauftrag, bevor du seine Daten löschst.',
  'The saved training directory is not a safe local job directory.':
    'Das gespeicherte Trainingsverzeichnis ist kein sicheres lokales Auftragsverzeichnis.',
  'A running training job uses this checkpoint. Stop it before deleting the data.':
    'Ein laufender Trainingsauftrag verwendet diesen Kontrollpunkt. Beende ihn, bevor du die Daten löschst.',
  "Another saved optimization job uses this job's study. Delete the dependent optimization job first.":
    'Ein anderer gespeicherter Optimierungsauftrag verwendet diese Studie. Lösche zuerst den abhängigen Optimierungsauftrag.',
  'The training data could not be completely removed. Try again.':
    'Die Trainingsdaten konnten nicht vollständig entfernt werden. Versuche es erneut.',
  'Model network and training': 'Modellnetzwerk und Training',
  'Policy / value · {channels} channels · {blocks} blocks':
    'Strategie / Bewertung · {channels} Kanäle · {blocks} Blöcke',
  'Training iteration': 'Trainingsiteration',
  '{iteration} / {total} planned': '{iteration} / {total} geplant',
  'Self-play': 'Selbstspiel',
  '{games} games / iteration': '{games} Spiele / Iteration',
  '{games} game / iteration': '{games} Spiel / Iteration',
  'Training search': 'Suche im Training',
  '{visits} MCTS iterations': '{visits} MCTS-Iterationen',
  '{seconds}s / move': '{seconds}s / Zug',
  Learning: 'Lernen',
  'LR {rate}': 'Lernrate {rate}',
  'Training batches': 'Trainingsbatches',
  '{epochs} epochs': '{epochs} Epochen',
  '{epochs} epoch': '{epochs} Epoche',
  'batch {batch}': 'Batch {batch}',
};
export default translations;
