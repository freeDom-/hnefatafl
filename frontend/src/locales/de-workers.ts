const translations: Record<string, string> = {
  'Remote workers': 'Externe Worker',
  'Connect cloud machines to train several models at the same time. Progress and models return here.':
    'Verbinde Cloud-Rechner, um mehrere Modelle gleichzeitig zu trainieren. Fortschritt und Modelle werden hier gespeichert.',
  'Connect a worker': 'Worker verbinden',
  '{active} / {capacity} jobs running': '{active} / {capacity} Aufträge aktiv',
  'Waiting for the first connection': 'Wartet auf die erste Verbindung',
  'Last seen {time}': 'Zuletzt verbunden: {time}',
  'Not connected yet': 'Noch nicht verbunden',
  'Revoke worker {name}': 'Zugriff für Worker {name} widerrufen',
  'Revoke access': 'Zugriff widerrufen',
  'No workers connected. Create a worker credential, then run the worker command on your cloud machine.':
    'Keine Worker verbunden. Erstelle einen Worker-Zugang und starte den Worker-Befehl auf deinem Cloud-Rechner.',
  'Revoke worker {name}?': 'Zugriff für Worker {name} widerrufen?',
  'This credential will stop working. Assigned running jobs will stop accepting updates and keep their last uploaded checkpoint. Queued jobs assigned to this worker are cancelled.':
    'Dieser Zugang wird ungültig. Zugewiesene laufende Aufträge akzeptieren keine weiteren Meldungen und behalten ihren zuletzt übertragenen Kontrollpunkt. Wartende Aufträge für diesen Worker werden abgebrochen.',
  'Connect a remote worker': 'Externen Worker verbinden',
  'Close worker setup': 'Worker-Einrichtung schließen',
  'Give this machine a name and choose how many independent training jobs it may run.':
    'Benenne diesen Rechner und wähle, wie viele unabhängige Trainingsaufträge er gleichzeitig ausführen darf.',
  'Worker name': 'Worker-Name',
  'Parallel jobs': 'Parallele Aufträge',
  'Create worker': 'Worker erstellen',
  'Creating…': 'Wird erstellt…',
  'Install this version of Hnefatafl with training dependencies on the cloud machine. Use its CPU or install the matching PyTorch build for its GPU.':
    'Installiere diese Hnefatafl-Version mit Trainingsabhängigkeiten auf dem Cloud-Rechner. Nutze dessen CPU oder installiere die passende PyTorch-Ausgabe für seine GPU.',
  'Worker token': 'Worker-Token',
  'Show token': 'Token anzeigen',
  'Copy token': 'Token kopieren',
  Copied: 'Kopiert',
  'Copy failed. Reveal and copy the token manually.':
    'Kopieren fehlgeschlagen. Zeige den Token an und kopiere ihn manuell.',
  'Save this token now. It is shown only once and is not saved in this browser. Keep it private.':
    'Sichere diesen Token jetzt. Er wird nur einmal angezeigt und nicht im Browser gespeichert. Halte ihn geheim.',
  'On the worker, read the token into an environment variable, then start the client:':
    'Lies den Token auf dem Worker in eine Umgebungsvariable ein und starte dann den Client:',
  'The worker must reach this server URL through your private network or HTTPS proxy. It only makes outbound requests; no worker port needs to be opened.':
    'Der Worker muss diese Server-URL über dein privates Netzwerk oder einen HTTPS-Proxy erreichen. Er sendet nur ausgehende Anfragen; auf dem Worker muss kein Port geöffnet werden.',
  Done: 'Fertig',
  'Run training on': 'Ausführungsort',
  'This server': 'Dieser Server',
  'Any remote worker': 'Beliebiger externer Worker',
  'Unavailable worker': 'Worker nicht verfügbar',
  'Training queued. An available remote worker will pick it up.':
    'Training eingereiht. Ein verfügbarer externer Worker übernimmt den Auftrag.',
  'Training runs on the selected worker': 'Das Training läuft auf dem ausgewählten Worker',
  'Parameter tuning runs on this server. Remote workers support model training.':
    'Die Parameteroptimierung läuft auf diesem Server. Externe Worker unterstützen Modelltraining.',
  'Jobs wait in the queue until a compatible worker has capacity. You can queue several runs with different settings.':
    'Aufträge warten, bis ein geeigneter Worker freie Kapazität hat. Du kannst mehrere Läufe mit unterschiedlichen Einstellungen einreihen.',
  'Queue training': 'Training einreihen',
  'Remote queue': 'Externe Warteschlange',
  'Waiting for an available worker': 'Wartet auf einen verfügbaren Worker',
  'Last update {time}': 'Letzte Meldung: {time}',
  'Choose an available remote worker.': 'Wähle einen verfügbaren externen Worker.',
  'Remote worker not found.': 'Externer Worker nicht gefunden.',
  'Worker names must contain 1–80 characters.': 'Worker-Namen müssen 1–80 Zeichen enthalten.',
  'The remote worker was revoked.': 'Der Zugriff des externen Workers wurde widerrufen.',
  'The remote worker stopped reporting before its lease expired. Resume a retained checkpoint to continue.':
    'Der externe Worker meldet sich nicht mehr und seine Zuweisung ist abgelaufen. Setze einen gespeicherten Kontrollpunkt fort, um weiterzutrainieren.',
  'Training execution must be local or remote.':
    'Das Training muss lokal oder auf einem externen Worker ausgeführt werden.',
  'Select a remote worker only for remote training.':
    'Wähle einen externen Worker nur für externes Training.',
  'Remote optimization is not supported. Run tuning on this server.':
    'Externe Parameteroptimierung wird nicht unterstützt. Führe die Optimierung auf diesem Server aus.',
  online: 'Verbunden',
  offline: 'Nicht verbunden',
  revoked: 'Widerrufen',
  queued: 'Eingereiht',
};
export default translations;
