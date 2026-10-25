const translations: Record<string, string> = {
  'Load editable parameters from an exported config.json. Checkpoint identity and the resume source are never replaced.':
    'Editierbare Parameter aus einer exportierten config.json laden. Kontrollpunkt-Identität und Fortsetzungsquelle werden nie ersetzt.',
  'Configuration imported. Review the values before continuing.':
    'Konfiguration importiert. Prüfe die Werte, bevor du fortsetzt.',
  'The imported iteration target was kept at the current value because it does not exceed the saved completed iteration.':
    'Das importierte Iterationsziel wurde beim aktuellen Wert belassen, da es die gespeicherte abgeschlossene Iteration nicht überschreitet.',
  'Checkpoint settings kept: {fields}.': 'Einstellungen des Kontrollpunkts beibehalten: {fields}.',
  'Fields that are not resume parameters were ignored: {fields}.':
    'Felder, die keine Fortsetzungsparameter sind, wurden ignoriert: {fields}.',
  'Resume training': 'Training fortsetzen',
  'Close resume training': 'Trainingsfortsetzung schließen',
  'Checking the saved training checkpoint…': 'Gespeicherter Trainingsstand wird geprüft…',
  'Completed iterations': 'Abgeschlossene Iterationen',
  Network: 'Netzwerk',
  'Continues the latest full checkpoint, including optimizer state, replay, random state and learning history. The architecture is kept. You can adjust the learning rate, weight decay and SGD momentum below.':
    'Setzt den neuesten vollständigen Kontrollpunkt fort, einschließlich Optimiererzustand, Trainingspuffer, Zufallszustand und Lernverlauf. Die Architektur bleibt erhalten. Lernrate, Gewichtsverfall und SGD-Momentum können unten angepasst werden.',
  'Continues the latest full checkpoint, including optimizer state, replay, random state and learning history.':
    'Setzt den neuesten vollständigen Kontrollpunkt fort, einschließlich Optimiererzustand, Trainingspuffer, Zufallszustand und Lernverlauf.',
  'Weight decay': 'Gewichtsverfall',
  'Momentum applies to SGD only. Adam and AdamW keep their saved moments.':
    'Momentum gilt nur für SGD. Adam und AdamW behalten ihre gespeicherten Momente.',
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
  'Resume accepts only total iterations, execution target and optimizer settings.':
    'Beim Fortsetzen können nur Gesamtzahl der Iterationen, Ausführungsziel und Optimierereinstellungen angegeben werden.',
  'learning_rate must be a number.': 'Die Lernrate muss eine Zahl sein.',
  'weight_decay must be a number.': 'Der Gewichtsverfall muss eine Zahl sein.',
  'momentum must be a number.': 'Das Momentum muss eine Zahl sein.',
  'learning_rate must be between 1e-07 and 1.0':
    'Die Lernrate muss zwischen 0,0000001 und 1 liegen.',
  'weight_decay must be between 0.0 and 1.0': 'Der Gewichtsverfall muss zwischen 0 und 1 liegen.',
  'momentum must be between 0.0 and 1.0': 'Das Momentum muss zwischen 0 und 1 liegen.',
  'Target total iterations must be an integer between 1 and 100000.':
    'Die Gesamtzahl der Zieliterationen muss eine ganze Zahl zwischen 1 und 100000 sein.',
  'Target total iterations must exceed the saved completed iteration.':
    'Die Gesamtzahl der Zieliterationen muss größer als die gespeicherte abgeschlossene Iteration sein.',
};
export default translations;
