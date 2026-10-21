const translations: Record<string, string> = {
  'Resume training': 'Training fortsetzen',
  'Close resume training': 'Trainingsfortsetzung schließen',
  'Checking the saved training checkpoint…': 'Gespeicherter Trainingsstand wird geprüft…',
  'Completed iterations': 'Abgeschlossene Iterationen',
  Network: 'Netzwerk',
  'Continues the latest full checkpoint from this training job, including optimizer, replay, random state and learning history. The saved architecture and training settings are kept.':
    'Setzt den neuesten vollständigen Kontrollpunkt dieses Trainingsauftrags fort, einschließlich Optimierer, Trainingspuffer, Zufallszustand und Lernverlauf. Architektur und Trainingseinstellungen bleiben erhalten.',
  'Target total iterations': 'Ziel: Iterationen insgesamt',
  'This is the total iteration count, not the number of extra iterations.':
    'Dies ist die Gesamtzahl der Iterationen, nicht die Zahl zusätzlicher Iterationen.',
  'No resumable full training checkpoint is available.':
    'Es ist kein vollständiger Trainingskontrollpunkt zum Fortsetzen verfügbar.',
  'Resuming…': 'Wird fortgesetzt…',
  'Continue training': 'Training fortsetzen',
  'Resume training for {name}': 'Training für {name} fortsetzen',
  'Training resumed from iteration {completed} toward {target}.':
    'Training von Iteration {completed} bis zum Ziel {target} fortgesetzt.',
  'Resume from checkpoint': 'Vom Kontrollpunkt fortsetzen',
  'The saved checkpoint was not found.': 'Der gespeicherte Kontrollpunkt wurde nicht gefunden.',
  'Optimization jobs use Resume study instead.':
    'Optimierungsaufträge werden über „Studie fortsetzen“ fortgesetzt.',
  'Stop this training job before resuming its checkpoint.':
    'Beende diesen Trainingsauftrag, bevor du seinen Kontrollpunkt fortsetzt.',
  'The saved file is not a full resumable training checkpoint.':
    'Die gespeicherte Datei ist kein vollständiger Trainingskontrollpunkt zum Fortsetzen.',
  'The saved training checkpoint could not be read safely.':
    'Der gespeicherte Trainingskontrollpunkt konnte nicht sicher gelesen werden.',
  'Resume accepts only the target total iterations.':
    'Beim Fortsetzen kann nur die Gesamtzahl der Zieliterationen angegeben werden.',
  'Target total iterations must be an integer between 1 and 100000.':
    'Die Gesamtzahl der Zieliterationen muss eine ganze Zahl zwischen 1 und 100000 sein.',
  'Target total iterations must exceed the saved completed iteration.':
    'Die Gesamtzahl der Zieliterationen muss größer als die gespeicherte abgeschlossene Iteration sein.',
};
export default translations;
