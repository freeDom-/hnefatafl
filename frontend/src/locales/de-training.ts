const translations: Record<string, string> = {
  'Delete entire tuning study': 'Gesamte Optimierungsstudie löschen',
  'Delete entire tuning study?': 'Gesamte Optimierungsstudie löschen?',
  'Delete entire study': 'Gesamte Studie löschen',
  'Permanently delete this study and every listed tuning job? This cannot be undone.':
    'Diese Studie und alle aufgeführten Optimierungsaufträge dauerhaft löschen? Dies kann nicht rückgängig gemacht werden.',
  'Loading study deletion preview…': 'Löschvorschau für die Studie wird geladen…',
  'Study owner': 'Studieneigentümer',
  'Study identity': 'Studienidentität',
  'Legacy identity unavailable': 'Identität der Altstudie nicht verfügbar',
  'All files in these directories will be removed, including the study database, trial checkpoints, replay buffers, results, history and logs.':
    'Alle Dateien in diesen Verzeichnissen werden entfernt, einschließlich Studiendatenbank, Versuchsprüfpunkten, Erfahrungsspeichern, Ergebnissen, Verlauf und Protokollen.',
  'Source training jobs, registered models, saved games and tournaments are kept.':
    'Ursprüngliche Trainingsaufträge, registrierte Modelle, gespeicherte Spiele und Turniere bleiben erhalten.',
  'Preserved source jobs': 'Erhaltene Quellaufträge',
  'Affected tuning jobs': 'Betroffene Optimierungsaufträge',
  'External checkpoint dependencies': 'Externe Prüfpunktabhängigkeiten',
  'Worker still active; stop it before deletion.': 'Worker noch aktiv; vor dem Löschen stoppen.',
  'Stop participating jobs and remove or rebase external checkpoint dependencies before deleting this study.':
    'Vor dem Löschen dieser Studie beteiligte Aufträge stoppen und externe Prüfpunktabhängigkeiten entfernen oder auf andere Prüfpunkte umstellen.',
  'I confirm deletion of the entire study and all listed tuning jobs.':
    'Ich bestätige das Löschen der gesamten Studie und aller aufgeführten Optimierungsaufträge.',
  'Study deleted. Remaining quarantined files will be cleaned up on the next deletion preview or server restart.':
    'Studie gelöscht. Verbleibende Dateien im Quarantäneverzeichnis werden bei der nächsten Löschvorschau oder beim Serverneustart bereinigt.',
  'Study and contributing tuning jobs deleted. Source jobs, registered models and saved game history have been kept.':
    'Studie und beteiligte Optimierungsaufträge gelöscht. Quellaufträge, registrierte Modelle und gespeicherte Spielverläufe bleiben erhalten.',
  'Shared studies may block individual deletion. Use “Delete entire tuning study” to review and remove the whole study.':
    'Gemeinsam genutzte Studien können das Löschen einzelner Aufträge verhindern. Mit „Gesamte Optimierungsstudie löschen“ die gesamte Studie prüfen und entfernen.',
  skipped_no_winner: 'Übersprungen: kein Gewinner',
  'Relative contribution to Optuna’s aggregate score. Weights are normalized by their total; larger weights make an opponent more influential. They do not change evaluation game counts. Every trial uses the same configured weights.':
    'Relativer Beitrag zum Optuna-Gesamtwert. Gewichte werden durch ihre Summe normalisiert; höhere Gewichte geben einem Gegner mehr Einfluss. Sie ändern nicht die Anzahl der Auswertungspartien. Jeder Versuch verwendet dieselben konfigurierten Gewichte.',

  'Choose a fixed benchmark before starting tuning. Champion changes during training.':
    'Vor der Optimierung einen festen Benchmark wählen. Champion ändert sich während des Trainings.',
  'Historical objective policy: retained trial scores and rankings are shown unchanged. Further trials require verified study compatibility.':
    'Historische Zielfunktion: Gespeicherte Versuchswerte und Rangfolgen werden unverändert angezeigt. Weitere Versuche erfordern nachgewiesene Studienkompatibilität.',
  'Fixed evaluation opponents': 'Feste Auswertungsgegner',
  'Optional weighted objective: evaluate each trial’s best new candidate against these fixed references. Registered model weights stay fixed; network sizes may differ.':
    'Optionales gewichtetes Ziel: Den besten neuen Kandidaten jedes Versuchs gegen diese festen Referenzen auswerten. Registrierte Modellgewichte bleiben fest; Netzwerkgrößen dürfen sich unterscheiden.',
  'Correct the optional study JSON to edit fixed opponents.':
    'Das optionale Studien-JSON korrigieren, um feste Gegner zu bearbeiten.',
  'Fixed opponent': 'Fester Gegner',
  'Opponent weight': 'Gegnergewicht',
  'Remove opponent': 'Gegner entfernen',
  'Add fixed opponent': 'Festen Gegner hinzufügen',
  'Select a compatible registered model or heuristic':
    'Kompatibles registriertes Modell oder Heuristik auswählen',
  'The selected frozen model is missing or has an incompatible battlefield.':
    'Das ausgewählte feste Modell fehlt oder hat ein inkompatibles Schlachtfeld.',
  'Optimization uses fixed references for comparable objectives. Champion evolves during training and is unavailable for tuning.':
    'Die Optimierung nutzt feste Referenzen für vergleichbare Ziele. Champion verändert sich während des Trainings und steht für die Optimierung nicht zur Verfügung.',
  'Frozen neural model': 'Festes neuronales Modell',
  'Running optimization studies': 'Laufende Optimierungsstudien',
  'Stop these studies before deleting their fixed opponent.':
    'Diese Studien vor dem Löschen ihres festen Gegners stoppen.',
  'Optuna objective score': 'Optuna-Zielwert',
  'Best in-training benchmark score': 'Bester Benchmarkwert während des Trainings',
  'Weighted aggregate score': 'Gewichteter Gesamtwert',
  'Trial pages': 'Versuchsseiten',
  'Locate winning trial': 'Gewinnenden Versuch anzeigen',
  trials: 'Versuche',
  'Optuna optimizes the objective score: the best newly evaluated candidate benchmark, or its weighted aggregate when fixed opponents are configured. Historical source scores are separate references.':
    'Optuna optimiert den Zielwert: den besten neu ausgewerteten Kandidaten-Benchmark oder dessen gewichteten Gesamtwert bei konfigurierten festen Gegnern. Historische Quellwerte sind separate Referenzen.',
  'The source benchmark is higher than the winning candidate benchmark. The objective still measures the newly trained candidate; the source remains unchanged.':
    'Der Quell-Benchmark ist höher als der Benchmark des gewinnenden Kandidaten. Das Ziel misst weiterhin den neu trainierten Kandidaten; die Quelle bleibt unverändert.',
  'Tuning charts': 'Optimierungsdiagramme',
  'Trial objective scores': 'Zielwerte der Versuche',
  'Best objective so far': 'Bisher bester Zielwert',
  'Trial runtime comparisons': 'Laufzeitvergleich der Versuche',
  'Parameter versus objective': 'Parameter gegen Zielwert',
  'Exploratory parameter importance': 'Explorative Parameterwichtigkeit',
  Seconds: 'Sekunden',
  'Charts show completed objective values. Large studies are sampled for display; the trial table contains all observations.':
    'Diagramme zeigen abgeschlossene Zielwerte. Große Studien werden zur Anzeige ausgedünnt; die Versuchstabelle enthält alle Beobachtungen.',
  'Relative binned variance explained, with at least 20 completed trials. Exploratory associations can be confounded and do not establish causality or statistical significance.':
    'Relativ erklärte Varianz in Gruppen ab mindestens 20 abgeschlossenen Versuchen. Explorative Zusammenhänge können verzerrt sein und belegen weder Kausalität noch statistische Signifikanz.',
  'Importance requires at least 20 completed trials, varying parameters and varying objectives. Fewer observations are insufficient.':
    'Die Wichtigkeit erfordert mindestens 20 abgeschlossene Versuche sowie unterschiedliche Parameter und Zielwerte. Weniger Beobachtungen reichen nicht aus.',
  'Verification status': 'Verifizierungsstatus',
  not_requested: 'Nicht angefordert',

  'Parameter changes': 'Parameteränderungen',
  'Optimized value': 'Optimierter Wert',
  'Current value': 'Aktueller Wert',
  'Review changes before continuing. Highlighted settings differ from the source checkpoint.':
    'Änderungen vor dem Fortsetzen prüfen. Hervorgehobene Einstellungen unterscheiden sich vom Quellprüfpunkt.',

  'CPU search processes (0 disables)': 'CPU-Suchprozesse (0 deaktiviert)',
  'Import config.json': 'config.json importieren',
  'Importing configuration…': 'Konfiguration wird importiert…',
  'Load parameters from an exported config.json. Review them before starting; importing never starts a job.':
    'Parameter aus einer exportierten config.json laden. Prüfe sie vor dem Start; der Import startet nie einen Auftrag.',
  'Configuration imported. Review the values before starting.':
    'Konfiguration importiert. Prüfe die Werte vor dem Start.',
  'Fields that are not training parameters were ignored: {fields}.':
    'Felder, die keine Trainingsparameter sind, wurden ignoriert: {fields}.',
  'Invalid configuration values: {fields}.': 'Ungültige Konfigurationswerte: {fields}.',
  'Training symmetry augmentation': 'Symmetrie-Datenaugmentation',
  Disabled: 'Deaktiviert',
  'Random D4 rotations and reflections': 'Zufällige D4-Drehungen und -Spiegelungen',
  'Random D4': 'Zufällig (D4)',
  Mode: 'Modus',
  'Randomly rotate or reflect training positions and their corresponding move targets to help the network learn equivalent board orientations.':
    'Trainingspositionen und die zugehörigen Zugwahrscheinlichkeiten werden zufällig gedreht oder gespiegelt, damit das Netz gleichwertige Brettausrichtungen erlernt.',
  'This changes training samples only, not the game rules or MCTS search budget.':
    'Das betrifft nur Trainingsbeispiele, nicht die Spielregeln oder das MCTS-Suchbudget.',
  'This target does not support training symmetry augmentation. Choose a newer worker or disable it.':
    'Dieses Ziel unterstützt keine Symmetrie-Datenaugmentation. Wähle einen neueren Worker oder deaktiviere sie.',
  'Symmetry augmentation (seconds)': 'Symmetrie-Augmentation (Sekunden)',
  'Training exposures': 'Trainingspräsentationen',
  'Augmented exposures': 'Augmentierte Präsentationen',
  'Identity transformations': 'Identitätstransformationen',
  'Share of optimization time': 'Anteil an der Optimierungszeit',
  'Inference cache': 'Inferenzcache',
  'Off · default': 'Aus · Standard',
  'Exact positions': 'Identische Stellungen',
  'Inference cache capacity': 'Inferenzcache-Kapazität',
  'Reuses identical positions during self-play. Measure throughput before enabling for longer runs.':
    'Verwendet Ergebnisse für identische Stellungen im Selbstspiel erneut. Miss den Durchsatz vor längeren Läufen.',
  'Inference requests': 'Inferenzanfragen',
  'MCTS neural requests': 'Neuronale MCTS-Anfragen',
  'MCTS node predictions reused': 'Wiederverwendete MCTS-Knotenvorhersagen',
  'Inference cache hits': 'Inferenzcache-Treffer',
  'Inference cache misses': 'Inferenzcache-Fehlversuche',
  'Batch duplicates eliminated': 'Entfernte Stapelduplikate',
  'Inference cache hit rate': 'Inferenzcache-Trefferquote',
  'Inference cache entries': 'Inferenzcache-Einträge',
  'Peak inference cache entries': 'Maximale Inferenzcache-Einträge',
  'Cache lookup (seconds)': 'Cache-Abfrage (Sekunden)',

  'Import job': 'Auftrag importieren',
  'Export job': 'Auftrag exportieren',
  'Importing job…': 'Auftrag wird importiert…',
  'Exporting job…': 'Auftrag wird exportiert…',
  'Job export failed.': 'Auftrag konnte nicht exportiert werden.',
  'Job imported. Review its history or resume from its full checkpoint.':
    'Auftrag importiert. Prüfe den Verlauf oder setze das Training vom vollständigen Kontrollpunkt fort.',
  'Training configuration': 'Trainingskonfiguration',
  'CRAFT A WORTHY OPPONENT': 'ERSCHAFFE EINEN WÜRDIGEN GEGNER',
  'The AI workshop.': 'Die KI-Werkstatt.',
  'From first principles to your own strategist. Train, evaluate, and bring it to the table.':
    'Vom ersten Versuch zum eigenen Strategen. Trainiere, bewerte und bringe ihn ans Brett.',
  'Dismiss notice': 'Hinweis schließen',
  'TRAINING DEVICE': 'TRAININGSGERÄT',
  'ACTIVE JOBS': 'AKTIVE AUFTRÄGE',
  'Training runs independently of your games': 'Das Training läuft unabhängig von deinen Spielen',
  'TRAINED MODELS': 'TRAINIERTE MODELLE',
  'Ready to join a compatible battlefield': 'Bereit für ein passendes Schlachtfeld',
  'Forge a new strategist': 'Schmiede einen neuen Strategen',
  'SELF-PLAY': 'SELBSTSPIEL',
  'A policy and value network learns from its own games. Small runs verify the workflow; strength takes time and evaluation.':
    'Ein Strategie- und Bewertungsnetz lernt aus eigenen Partien. Kleine Läufe prüfen den Ablauf; Spielstärke braucht Zeit und Auswertung.',
  'Train a model': 'Modell trainieren',
  'Tune parameters': 'Parameter optimieren',
  Battlefield: 'Schlachtfeld',
  'Training preset': 'Trainingsvorlage',
  'Recommended · tailored to this battlefield': 'Empfohlen · auf dieses Schlachtfeld abgestimmt',
  'Quick check · a tiny real training run': 'Kurztest · ein kleiner echter Trainingslauf',
  'Beginner · a starting point': 'Einstieg · ein Ausgangspunkt',
  'Balanced · a longer experiment': 'Ausgewogen · ein längeres Experiment',
  'Model name': 'Modellname',
  'Suggest Viking name': 'Wikingernamen vorschlagen',
  'Suggest a Viking name': 'Einen Wikingernamen vorschlagen',
  Device: 'Gerät',
  Automatic: 'Automatisch',
  CPU: 'CPU',
  'NVIDIA CUDA': 'NVIDIA CUDA',
  unavailable: 'nicht verfügbar',
  'Apple Metal': 'Apple Metal',
  'Training iterations': 'Trainingsiterationen',
  'Self-play games per iteration': 'Selbstspielpartien pro Iteration',
  'Batch size': 'Stapelgröße',
  'MCTS iterations per move': 'MCTS-Iterationen pro Zug',
  'channels ·': 'Kanäle ·',
  'residual blocks ·': 'Residualblöcke ·',
  'self-play games.': 'Selbstspielpartien.',
  'A board-specific starting budget, not a proven strongest configuration. Full runs can take hours or days; choose Quick check to verify your setup.':
    'Ein auf die Brettgröße abgestimmtes Startbudget, keine nachweislich stärkste Konfiguration. Vollständige Läufe können Stunden oder Tage dauern; prüfe deine Einrichtung zunächst mit dem Kurztest.',
  'Save every new best model': 'Jedes neue beste Modell speichern',
  'No new best model was produced. The existing registered model was reused.':
    'Es wurde kein neues bestes Modell erzeugt. Das vorhandene registrierte Modell wurde wiederverwendet.',
  'The trained weights already matched a registered model, which was reused.':
    'Die trainierten Gewichte entsprachen bereits einem registrierten Modell, das wiederverwendet wurde.',
  'The existing registered model was reused.':
    'Das vorhandene registrierte Modell wurde wiederverwendet.',
  'Retain and register each strict evaluation improvement. Tied scores do not create extra models. Uses additional disk space.':
    'Jede echte Verbesserung der Bewertung speichern und registrieren. Gleiche Punktzahlen erzeugen keine zusätzlichen Modelle. Benötigt mehr Speicherplatz.',
  'Advanced configuration': 'Erweiterte Konfiguration',
  'Network channels': 'Netzwerkkanäle',
  'Residual blocks': 'Residualblöcke',
  Optimizer: 'Optimierer',
  AdamW: 'AdamW',
  Adam: 'Adam',
  SGD: 'SGD',
  'Epochs per iteration': 'Epochen pro Iteration',
  'Learning rate': 'Lernrate',
  Momentum: 'Momentum',
  'Search time limit (seconds)': 'Suchzeitlimit (Sekunden)',
  'MCTS exploration': 'MCTS-Erkundung',
  'Self-play temperature': 'Selbstspieltemperatur',
  'Exploration noise alpha': 'Erkundungsrauschen Alpha',
  'Exploration noise fraction': 'Anteil des Erkundungsrauschens',
  'Replay buffer positions': 'Positionen im Wiederholungspuffer',
  'Weight regularization': 'Gewichtsregularisierung',
  'Checkpoint every (iterations)': 'Kontrollpunkt alle (Iterationen)',
  'Evaluate every (iterations)': 'Bewerten alle (Iterationen)',
  'Evaluation games (even)': 'Bewertungspartien (gerade Anzahl)',
  'Evaluation search iterations': 'Suchiterationen für die Bewertung',
  'Self-play move limit': 'Zuglimit im Selbstspiel',
  'Evaluation move limit': 'Zuglimit der Bewertung',
  'CPU threads': 'CPU-Threads',
  'Random seed': 'Zufallsstartwert',
  'Mixed precision when supported': 'Gemischte Genauigkeit, falls unterstützt',
  'Trials optimize game results against a baseline. Tuning can take much longer than a single run. Resume a saved study from its job below.':
    'Versuche optimieren die Ergebnisse gegen einen festen Vergleichsgegner. Die Optimierung kann deutlich länger als ein einzelner Lauf dauern. Gespeicherte Studien lassen sich unten fortsetzen.',
  'Trial budget': 'Anzahl der Versuche',
  'Study name': 'Studienname',
  'Parameter search space (JSON)': 'Parametersuchraum (JSON)',
  'Start small. Measure improvement.': 'Beginne klein. Miss die Verbesserung.',
  'At the forge': 'An der Schmiede',
  Refresh: 'Aktualisieren',
  'Your next opponent starts here.': 'Hier entsteht dein nächster Gegner.',
  'Start a quick check to run real self-play, training, evaluation, and checkpoint creation.':
    'Starte einen Kurztest mit echtem Selbstspiel, Training, Bewertung und Kontrollpunkten.',
  'Training progress': 'Trainingsfortschritt',
  'best models retained during this run.': 'beste Modelle während dieses Laufs gespeichert.',
  'Evaluation metrics': 'Bewertungskennzahlen',
  'Training log': 'Trainingsprotokoll',
  'YOUR STRATEGISTS': 'DEINE STRATEGEN',
  'Model collection': 'Modellsammlung',
  'Compatible checkpoints are validated before registration. Select a trained model under “Neural network” when starting a game.':
    'Kompatible Kontrollpunkte werden vor der Registrierung geprüft. Wähle beim Spielstart unter „Neuronales Netz“ ein trainiertes Modell.',
  'Training seed': 'Trainingsstartwert',
  'Delete model': 'Modell löschen',
  AGAINST: 'GEGEN',
  BASELINE: 'VERGLEICHSGEGNER',
  baseline: 'Vergleichsgegner',
  previous_model: 'vorheriges Modell',
  wins: 'Siege',
  draws: 'Unentschieden',
  losses: 'Niederlagen',
  'unfinished games · limited evaluation': 'unvollendete Partien · begrenzte Bewertung',
  'Your trained and imported models will live here.':
    'Hier erscheinen deine trainierten und importierten Modelle.',
  'This removes': 'Dies entfernt',
  'from your model collection and opponent selection. Completed game histories, statistics and training checkpoints are kept.':
    'aus deiner Modellsammlung und Gegnerauswahl. Abgeschlossene Spielverläufe, Statistiken und Trainingskontrollpunkte bleiben erhalten.',
  'Checking saved games and tournaments…': 'Gespeicherte Partien und Turniere werden geprüft…',
  'Check again': 'Erneut prüfen',
  'Running tournament': 'Laufendes Turnier',
  'Stop these tournaments in Tournaments before deleting this model.':
    'Beende diese Turniere in der Turnieransicht, bevor du das Modell löschst.',
  unfinished: 'unvollendete',
  game: 'Partie',
  games: 'Partien',
  'Saved games remain on this server even when they are no longer open in your browser. Continue them from the Hall of records, or discard them here.':
    'Gespeicherte Partien bleiben auf diesem Server, auch wenn sie nicht mehr im Browser geöffnet sind. Setze sie in der Ruhmeshalle fort oder verwirf sie hier.',
  vs: 'gegen',
  'moves ·': 'Züge ·',
  'Discard unfinished games and delete model': 'Unvollendete Partien verwerfen und Modell löschen',
  'Discarded games are permanently removed with no result, history or statistics. Completed games and training checkpoints remain.':
    'Verworfene Partien werden ohne Ergebnis, Verlauf oder Statistik endgültig entfernt. Abgeschlossene Partien und Trainingskontrollpunkte bleiben erhalten.',
  'No unfinished games use this model. Saved games and training checkpoints remain available.':
    'Keine unvollendete Partie nutzt dieses Modell. Gespeicherte Partien und Trainingskontrollpunkte bleiben verfügbar.',
  'Not recorded': 'Nicht aufgezeichnet',
  'No log output yet.': 'Noch keine Protokollausgabe.',
  'Export {name}': '{name} exportieren',
  'Delete model {name}': 'Modell {name} löschen',
  'Delete model {name}?': 'Modell {name} löschen?',
  'Importing…': 'Wird importiert…',
  '↑ Import model': '↑ Modell importieren',
  'Stopping…': 'Wird beendet…',
  'Stop training': 'Training beenden',
  'Resume study': 'Studie fortsetzen',
  'Resume from checkpoint': 'Ab Kontrollpunkt fortsetzen',
  'Starting…': 'Wird gestartet…',
  'Start training': 'Training starten',
  'Start tuning': 'Optimierung starten',
  'CUDA available': 'CUDA verfügbar',
  'CPU training available': 'CPU-Training verfügbar',
  'NVIDIA acceleration is available': 'NVIDIA-Beschleunigung ist verfügbar',
  'Automatic CPU fallback · CUDA supported': 'Automatischer CPU-Rückfall · CUDA unterstützt',
  'Your training job has started. You can keep playing while it runs.':
    'Dein Training wurde gestartet. Du kannst währenddessen weiterspielen.',
  'This job has no saved checkpoint yet.':
    'Dieser Auftrag hat noch keinen gespeicherten Kontrollpunkt.',
  'Model imported. It is now available in compatible games.':
    'Modell importiert. Es ist jetzt in kompatiblen Partien verfügbar.',
  'Model deleted. {count} unfinished games removed without results. Completed history and training checkpoints have been kept.':
    'Modell gelöscht. {count} unvollendete Partien ohne Ergebnis entfernt. Abgeschlossene Verläufe und Trainingskontrollpunkte bleiben erhalten.',
  'Model deleted. {count} unfinished game removed without results. Completed history and training checkpoints have been kept.':
    'Modell gelöscht. {count} unvollendete Partie ohne Ergebnis entfernt. Abgeschlossene Verläufe und Trainingskontrollpunkte bleiben erhalten.',
  'Model deleted. Saved game history and training checkpoints have been kept.':
    'Modell gelöscht. Gespeicherte Spielverläufe und Trainingskontrollpunkte bleiben erhalten.',
  'Training job': 'Trainingsauftrag',
  'Policy / value': 'Strategie / Bewertung',
  'Iteration {iteration}': 'Iteration {iteration}',
  'Graphs appear after the first completed training iteration.':
    'Diagramme erscheinen nach der ersten abgeschlossenen Trainingsiteration.',
  'Loss by iteration': 'Verlust nach Iteration',
  'Evaluation by iteration': 'Bewertung nach Iteration',
  'Policy loss': 'Strategieverlust',
  'Value loss': 'Bewertungsverlust',
  'Completion-aware score': 'Punktzahl mit unvollendeten Partien',
  'Training history graphs': 'Diagramme zum Trainingsverlauf',
  'Iteration {iteration}: {value}': 'Iteration {iteration}: {value}',
  'Lower loss means better fit to self-play targets, not necessarily stronger play. Evaluation gives unfinished games zero points.':
    'Ein niedrigerer Verlust bedeutet eine bessere Anpassung an Selbstspielziele, nicht unbedingt stärkere Spielweise. Unvollendete Bewertungspartien erhalten null Punkte.',
  'View graph data': 'Diagrammdaten anzeigen',
  'Training history data': 'Daten zum Trainingsverlauf',
  Iteration: 'Iteration',
  'Evaluation score': 'Bewertungspunktzahl',
  'Unfinished games': 'Unvollendete Partien',
  'policy loss': 'Strategieverlust',
  'value loss': 'Bewertungsverlust',
  'replay positions': 'Pufferpositionen',
  'total iterations': 'Iterationen gesamt',
  'games generated': 'Erzeugte Partien',
  iteration: 'Iteration',
  duration: 'Dauer',
  stage: 'Phase',
  device: 'Gerät',
  'mixed precision': 'Gemischte Genauigkeit',
  running: 'läuft',
  pending: 'ausstehend',
  queued: 'eingereiht',
  cancelling: 'wird beendet',
  cancelled: 'beendet',
  completed: 'abgeschlossen',
  failed: 'fehlgeschlagen',
  initializing: 'Vorbereitung',
  self_play: 'Selbstspiel',
  optimization: 'Optimierung',
  evaluation: 'Bewertung',
  iteration_complete: 'Iteration abgeschlossen',
  'Self-play execution': 'Selbstspiel-Ausführung',
  'Automatic · batched on CUDA/OpenVINO': 'Automatisch · gebündelt auf CUDA/OpenVINO',
  Sequential: 'Sequenziell',
  Batched: 'Gebündelt',
  Parallel: 'Parallel',
  'Concurrent self-play games': 'Gleichzeitige Selbstspiele',
  'Inference batch size': 'Inferenz-Bündelgröße',
  'Training performance': 'Trainingsleistung',
  'Self-play throughput': 'Selbstspiel-Durchsatz',
  'Inference batching': 'Inferenz-Bündelung',
  'Phase timings': 'Phasenzeiten',
  'Games / hour': 'Spiele / Stunde',
  'Positions / second': 'Positionen / Sekunde',
  'Simulations / second': 'Simulationen / Sekunde',
  'Neural evaluations / second': 'Netzauswertungen / Sekunde',
  'Average game length': 'Mittlere Spiellänge',
  'Completed games (including draws)': 'Abgeschlossene Spiele (einschließlich Remis)',
  'Drawn games': 'Remis',
  'Truncated games': 'Abgebrochene Spiele',
  'Actual inference batch size (average / min / max)':
    'Tatsächliche Inferenz-Bündelgröße (Mittel / Min. / Max.)',
  'Inference batches': 'Inferenz-Bündel',
  'Average batch latency (seconds)': 'Mittlere Bündellatenz (Sekunden)',
  'Self-play wall time (seconds)': 'Selbstspiel-Gesamtdauer (Sekunden)',
  'CPU search activity (seconds)': 'CPU-Suchaktivität (Sekunden)',
  'Inference host activity (seconds)': 'Inferenz-Hostaktivität (Sekunden)',
  'Inference CUDA activity (seconds)': 'Inferenz-CUDA-Aktivität (Sekunden)',
  'Gradient optimization (seconds)': 'Gradientenoptimierung (Sekunden)',
  'Opponent evaluation (seconds)': 'Gegnerbewertung (Sekunden)',
  'Iteration wall time (seconds)': 'Iterations-Gesamtdauer (Sekunden)',
  'Peak GPU allocated memory (MiB)': 'Maximal belegter GPU-Speicher (MiB)',
  'Peak GPU reserved memory (MiB)': 'Maximal reservierter GPU-Speicher (MiB)',
  'Throughput history': 'Durchsatzverlauf',
  'Wall-clock durations and accumulated activity times are reported separately; activities can overlap.':
    'Gesamtdauer und aufsummierte Aktivitätszeiten werden getrennt angezeigt; Aktivitäten können sich überlappen.',
  'Rename job': 'Auftrag umbenennen',
  'Rename training job': 'Trainingsauftrag umbenennen',
  'Close rename training job': 'Umbenennen des Trainingsauftrags schließen',
  'Job name': 'Auftragsname',
  'Renaming changes only the displayed job name. Its ID, checkpoint, parameters and execution are unchanged.':
    'Das Umbenennen ändert nur den angezeigten Auftragsnamen. ID, Kontrollpunkt, Parameter und Ausführung bleiben unverändert.',
  'Training job renamed. Its identity, checkpoint, parameters and execution are unchanged.':
    'Trainingsauftrag umbenannt. Identität, Kontrollpunkt, Parameter und Ausführung bleiben unverändert.',
  'Live phase progress': 'Live-Phasenfortschritt',
  'Phase progress': 'Phasenfortschritt',
  'Phase elapsed {time}': 'Phasendauer {time}',
  'Last progress {seconds}s ago': 'Letzter Fortschritt vor {seconds}s',
  'Worker active': 'Worker aktiv',
  'Running, long operation': 'Läuft, langwieriger Schritt',
  'Potentially stalled': 'Möglicherweise hängend',
  'No recent updates': 'Keine aktuellen Meldungen',
  'Worker connection lost': 'Worker-Verbindung unterbrochen',
  'Monitoring connection unavailable': 'Monitoring-Verbindung nicht verfügbar',
  'Iteration elapsed {time}': 'Iterationsdauer {time}',
  'Trial elapsed {time}': 'Versuchsdauer {time}',
  'The parameter search space is not valid JSON.': 'Der Parameter-Suchraum ist kein gültiges JSON.',
  'Waiting for a worker': 'Warten auf einen Worker',
  Games: 'Spiele',
  Epoch: 'Epoche',
  Opponent: 'Gegner',
  'MCTS simulations': 'MCTS-Simulationen',
  'Optimization steps': 'Optimierungsschritte',
  'Training was not started: one configuration field was rejected.':
    'Das Training wurde nicht gestartet: Ein Konfigurationsfeld wurde abgelehnt.',
  'Training was not started: {count} configuration fields were rejected.':
    'Das Training wurde nicht gestartet: {count} Konfigurationsfelder wurden abgelehnt.',
  'This server does not accept this training field. Remove it from the configuration and try again.':
    'Dieser Server akzeptiert dieses Trainingsfeld nicht. Entferne es aus der Konfiguration und versuche es erneut.',
  'The server assigns each training job its own output directory.':
    'Der Server weist jedem Trainingsauftrag ein eigenes Ausgabeverzeichnis zu.',
  'The server keeps trained models in its own model directory.':
    'Der Server speichert trainierte Modelle in seinem eigenen Modellverzeichnis.',

  // Field help: the shared training-parameter tooltips.
  'Explain {field}': '{field} erklären',
  'Where the job runs: this server, any compatible remote worker, or one specific worker. Remote jobs wait in the queue until a matching worker has capacity. The target determines which training devices and inference backends are available.':
    'Wo der Auftrag läuft: auf diesem Server, auf einem beliebigen kompatiblen Remote-Worker oder auf einem bestimmten Worker. Remote-Aufträge warten in der Warteschlange, bis ein passender Worker Kapazität hat. Das Ziel bestimmt, welche Trainingsgeräte und Inferenzsysteme verfügbar sind.',
  'Battlefield the model is trained for. Each battlefield has a different board size and starting position, so a model does not transfer directly between them. Changing this replaces the training values with the plan for the new battlefield.':
    'Schlachtfeld, für das das Modell trainiert wird. Jedes Schlachtfeld hat eine andere Brettgröße und Ausgangsstellung, daher lässt sich ein Modell nicht direkt zwischen ihnen übertragen. Eine Änderung ersetzt die Trainingswerte durch den Plan für das neue Schlachtfeld.',
  'Starting set of training values. Recommended applies the plan tailored to the selected battlefield, Quick check is a tiny real run for verifying the setup, and Beginner and Balanced are progressively longer experiments. Selecting a preset overwrites the training values below; the model name, random seed, device and inference settings are kept.':
    'Startwerte für das Training. Empfohlen verwendet den auf das gewählte Schlachtfeld zugeschnittenen Plan, Schnelltest ist ein winziger echter Lauf zum Prüfen des Aufbaus, und Anfänger und Ausgewogen sind zunehmend längere Experimente. Die Auswahl eines Presets überschreibt die Trainingswerte unten; Modellname, Zufallsstartwert sowie Geräte- und Inferenzeinstellungen bleiben erhalten.',
  'Name this model is registered and listed under, and the label stored in its checkpoints. Must contain 1–100 characters.':
    'Name, unter dem dieses Modell registriert und aufgelistet wird, und die Bezeichnung in seinen Kontrollpunkten. Muss 1–100 Zeichen enthalten.',
  'Device the training process runs on. Automatic picks NVIDIA CUDA when available, then Apple Metal, otherwise CPU. CUDA enables batched self-play and mixed precision; CPU training works everywhere but is slower. Apple Metal uses the integrated GPU on Apple silicon.':
    'Gerät, auf dem der Trainingsprozess läuft. Automatisch wählt NVIDIA CUDA, falls verfügbar, dann Apple Metal, sonst CPU. CUDA ermöglicht gebündeltes Selbstspiel und gemischte Präzision; CPU-Training funktioniert überall, ist aber langsamer. Apple Metal nutzt die integrierte GPU auf Apple-Silicon.',
  'Backend that evaluates the network during self-play. PyTorch is the default and runs anywhere. OpenVINO accelerates inference on a supported Intel GPU while the network’s weight updates stay in PyTorch on the CPU. Evaluation uses PyTorch inference by default, and “Use training inference backend” lets it reuse this backend, OpenVINO included. OpenVINO requires CPU training and a compatible execution environment with supported Intel hardware.':
    'System, das das Netz während des Selbstspiels auswertet. PyTorch ist der Standard und läuft überall. OpenVINO beschleunigt die Inferenz auf einer unterstützten Intel-GPU, während die Gewichtsaktualisierungen des Netzes in PyTorch auf der CPU bleiben. Die Auswertung nutzt standardmäßig PyTorch-Inferenz; „Inferenzsystem des Trainings verwenden“ lässt sie dieses System wiederverwenden, OpenVINO eingeschlossen. OpenVINO erfordert CPU-Training und eine kompatible Ausführungsumgebung mit unterstützter Intel-Hardware.',
  'Reuses neural evaluations of identical positions instead of recomputing them. Off disables caching. Exact positions keys results by ruleset, board and side to move, removes duplicates inside one batch, and is dropped whenever the model, backend or precision changes. Every phase builds its own cache, so nothing carries over between iterations; it pays off when the same position recurs, so measure throughput before relying on it.':
    'Verwendet neuronale Auswertungen identischer Stellungen erneut, statt sie neu zu berechnen. Aus deaktiviert den Cache. Identische Stellungen ordnet Ergebnisse nach Regelwerk, Brett und Zugseite zu, entfernt Duplikate innerhalb eines Stapels und wird verworfen, sobald sich Modell, System oder Präzision ändern. Jede Phase legt einen eigenen Cache an, sodass nichts zwischen Iterationen erhalten bleibt; das lohnt sich, wenn dieselbe Stellung wiederkehrt, also miss den Durchsatz, bevor du dich darauf verlässt.',
  'Maximum number of positions one phase’s cache keeps. The least recently used entry is evicted when it is full. A larger cache can raise the hit rate but holds more results in memory, and the value is ignored while the cache is off.':
    'Maximale Anzahl Stellungen, die der Cache einer Phase behält. Ist er voll, wird der am längsten nicht genutzte Eintrag verdrängt. Ein größerer Cache kann die Trefferquote erhöhen, hält aber mehr Ergebnisse im Speicher; bei deaktiviertem Cache wird der Wert ignoriert.',
  'Intel GPU that OpenVINO runs inference on. GPU selects the default device and a numbered entry a specific one. Only meaningful for the OpenVINO backend: PyTorch ignores it and uses the training device instead.':
    'Intel-GPU, auf der OpenVINO die Inferenz ausführt. GPU wählt das Standardgerät, ein nummerierter Eintrag ein bestimmtes. Nur für das OpenVINO-System relevant: PyTorch ignoriert den Wert und nutzt stattdessen das Trainingsgerät.',
  'Feature width of the network’s first convolution and of every residual block; both output heads also take their width from it. More channels raise capacity and memory use per evaluation and enlarge checkpoints, and usually need more self-play games to train well. It is part of the model architecture, so a resumed run must keep the same value.':
    'Merkmalsbreite der ersten Faltung des Netzes und jedes Residualblocks; auch beide Ausgabeköpfe beziehen ihre Breite daraus. Mehr Kanäle erhöhen Kapazität und Speicherbedarf jeder Auswertung und vergrößern Kontrollpunkte; sie brauchen meist mehr Selbstspielpartien zum Trainieren. Teil der Modellarchitektur, daher muss ein fortgesetzter Lauf denselben Wert behalten.',
  'Number of residual blocks in the network trunk. More blocks make the network deeper and more capable but raise the compute and memory of every evaluation. Zero keeps only the initial convolution stage. Part of the model architecture, so a resumed run must keep the same value.':
    'Anzahl der Residualblöcke im Netzwerkrumpf. Mehr Blöcke machen das Netz tiefer und leistungsfähiger, erhöhen aber Rechenaufwand und Speicherbedarf jeder Auswertung. Null behält nur die anfängliche Faltungsstufe. Teil der Modellarchitektur, daher muss ein fortgesetzter Lauf denselben Wert behalten.',
  'Replay positions used in one gradient update. Larger batches give smoother gradients and better device utilization but use more memory; smaller batches take more update steps over the same data. The optimizer steps per iteration are the number of epochs multiplied by the number of batches in the replay buffer, rounded up so the final, partly filled batch is trained as well.':
    'Stellungen aus dem Trainingspuffer pro Gewichtsaktualisierung. Größere Stapel ergeben glattere Gradienten und eine bessere Geräteauslastung, brauchen aber mehr Speicher; kleinere Stapel führen zu mehr Aktualisierungsschritten über dieselben Daten. Die Optimierungsschritte pro Iteration sind die Zahl der Epochen multipliziert mit der Zahl der Stapel im Trainingspuffer, aufgerundet, sodass auch der letzte, nur teilweise gefüllte Stapel trainiert wird.',
  'Gradient-training passes over the whole replay buffer per iteration. More epochs extract more from the collected positions but take longer and can overfit. Total passes are the iterations multiplied by this value.':
    'Durchläufe des Gradiententrainings über den gesamten Trainingspuffer pro Iteration. Mehr Epochen holen mehr aus den gesammelten Stellungen, dauern aber länger und können überanpassen. Die Gesamtzahl der Durchläufe ist die Zahl der Iterationen multipliziert mit diesem Wert.',
  'Step size the optimizer applies to the network weights. Higher values learn faster but can become unstable or prevent convergence; lower values are steadier but need more training. Reduce it if losses fluctuate strongly. On resume, changing it overrides the restored value while the accumulated optimizer state is kept. When automatic hyperparameter updates are enabled, this is the controller’s starting rate and must be one of the candidates; overriding it on resume moves the controller’s effective rate while its learned state is kept.':
    'Schrittweite, mit der der Optimierer die Netzgewichte anpasst. Höhere Werte lernen schneller, können aber instabil werden oder die Konvergenz verhindern; niedrigere Werte sind stabiler, brauchen aber mehr Training. Verringere den Wert, wenn die Verluste stark schwanken. Beim Fortsetzen überschreibt eine Änderung den wiederhergestellten Wert, während der akkumulierte Optimiererzustand erhalten bleibt. Sind automatische Hyperparameter-Aktualisierungen aktiv, ist dies die Startrate des Controllers und muss einer der Kandidaten sein; eine Änderung beim Fortsetzen verschiebt die effektive Rate des Controllers, während sein gelernter Zustand erhalten bleibt.',
  'Algorithm that updates the network weights. AdamW is Adam with decoupled weight decay and is the default; Adam adapts a step size per parameter; SGD uses the momentum setting and often needs a higher learning rate. The optimizer is part of the checkpoint identity, so a resumed run cannot change it.':
    'Algorithmus, der die Netzgewichte aktualisiert. AdamW ist Adam mit entkoppeltem Gewichtsverfall und der Standard; Adam passt die Schrittweite je Parameter an; SGD nutzt die Momentum-Einstellung und braucht oft eine höhere Lernrate. Der Optimierer gehört zur Kontrollpunkt-Identität, daher kann ein fortgesetzter Lauf ihn nicht ändern.',
  'Regularization the optimizer applies to the weights. Higher values shrink weights more strongly and reduce overfitting at the cost of slower fitting; 0 disables it. AdamW applies it decoupled from the gradient update, Adam and SGD apply it through the optimizer itself.':
    'Regularisierung, die der Optimierer auf die Gewichte anwendet. Höhere Werte schrumpfen die Gewichte stärker und reduzieren Überanpassung auf Kosten eines langsameren Anpassens; 0 deaktiviert sie. AdamW wendet sie entkoppelt von der Gradientenaktualisierung an, Adam und SGD über den Optimierer selbst.',
  'Momentum of the SGD optimizer, which carries previous updates forward. Higher values smooth the update direction and speed convergence but can overshoot; 0 is plain SGD. Ignored by Adam and AdamW, so it only matters when SGD is selected and it is disabled while resuming a non-SGD run.':
    'Momentum des SGD-Optimierers, das vorherige Aktualisierungen mitführt. Höhere Werte glätten die Aktualisierungsrichtung und beschleunigen die Konvergenz, können aber überschießen; 0 ist reines SGD. Von Adam und AdamW ignoriert, daher nur bei SGD relevant, und beim Fortsetzen eines Nicht-SGD-Laufs deaktiviert.',
  'Maximum number of self-play positions kept for gradient training. New positions are appended and the oldest are discarded once the cap is reached, so this bounds the data each epoch trains on and the memory it uses. A larger buffer keeps more history and diversifies batches; a smaller one keeps training focused on recent play.':
    'Maximale Anzahl Selbstspielstellungen, die fürs Gradiententraining behalten werden. Neue Stellungen werden angehängt, die ältesten verworfen, sobald die Grenze erreicht ist; das begrenzt die Daten je Epoche und den Speicherbedarf. Ein größerer Puffer behält mehr Verlauf und diversifiziert die Stapel; ein kleinerer konzentriert das Training auf aktuelle Partien.',
  'Runs the network and gradient updates in half precision when the training device is CUDA, speeding up training and reducing GPU memory use. On CPU and Apple Metal it has no effect. Numerical results can differ slightly from a full-precision run, so it is not bit-exact.':
    'Führt Netz und Gewichtsaktualisierungen in halber Präzision aus, wenn das Trainingsgerät CUDA ist; das beschleunigt das Training und senkt den GPU-Speicherbedarf. Auf CPU und Apple Metal hat es keine Wirkung. Die numerischen Ergebnisse können leicht von einem Lauf in voller Präzision abweichen, sind also nicht bitgenau.',
  'Seed for the training random number generators: model initialization, self-play move sampling, symmetry augmentation and the per-iteration and per-game streams derived from it. Repeating a run with the same seed replays the same random choices, so runs stay comparable, but GPU kernels, parallel search processes and floating-point reductions can still introduce small numerical differences. A resumed run must keep the checkpoint’s seed.':
    'Startwert für die Zufallsgeneratoren des Trainings: Modellinitialisierung, Zugauswahl im Selbstspiel, Symmetrie-Augmentation und die daraus abgeleiteten Ströme je Iteration und Partie. Ein erneuter Lauf mit demselben Startwert wiederholt dieselben Zufallsentscheidungen, sodass Läufe vergleichbar bleiben; GPU-Kernel, parallele Suchprozesse und Gleitkomma-Reduktionen können jedoch kleine numerische Unterschiede bewirken. Ein fortgesetzter Lauf muss den Startwert des Kontrollpunkts behalten.',
  'Number of CPU threads the training process lets PyTorch use. Higher values speed up CPU-bound work on a multi-core machine but compete with other jobs and the server. It is also passed to OpenVINO as the CPU inference thread count. CPU search workers ignore it and pin one thread each.':
    'Anzahl der CPU-Threads, die der Trainingsprozess PyTorch erlaubt. Höhere Werte beschleunigen CPU-lastige Arbeit auf Mehrkernrechnern, konkurrieren aber mit anderen Aufträgen und dem Server. Der Wert wird auch als CPU-Inferenz-Threadzahl an OpenVINO übergeben. CPU-Suchprozesse ignorieren ihn und binden je einen Thread.',
  'Number of extra CPU processes that run MCTS search during self-play and evaluation. 0 disables the pool. Workers start on demand, are capped by the phase’s game concurrency and are reused across phases. They are only used for CPU models with the PyTorch backend; more workers can raise CPU utilization and throughput but add memory and scheduling overhead.':
    'Anzahl zusätzlicher CPU-Prozesse, die während Selbstspiel und Auswertung die MCTS-Suche ausführen. 0 deaktiviert den Pool. Prozesse starten bei Bedarf, sind durch die Partienebenläufigkeit der Phase begrenzt und werden über Phasen hinweg wiederverwendet. Sie werden nur für CPU-Modelle mit dem PyTorch-System genutzt; mehr Prozesse können CPU-Auslastung und Durchsatz erhöhen, kosten aber Speicher und Planungsaufwand.',
  'Number of training iterations. Each iteration generates self-play games, trains the network from the replay buffer, optionally evaluates the model and writes a checkpoint. More iterations mean more self-play data and longer training; the final iteration always evaluates and checkpoints regardless of the frequency settings.':
    'Anzahl der Trainingsiterationen. Jede Iteration erzeugt Selbstspielpartien, trainiert das Netz aus dem Trainingspuffer, wertet das Modell optional aus und schreibt einen Kontrollpunkt. Mehr Iterationen bedeuten mehr Selbstspieldaten und längeres Training; die letzte Iteration wird unabhängig von den Intervall-Einstellungen immer ausgewertet und gespeichert.',
  'Writes the rolling last checkpoint every N iterations, so less progress is lost if a run is interrupted. The final iteration always writes. This is separate from best-model snapshots, which are written when evaluation records an improvement.':
    'Schreibt den rollierenden letzten Kontrollpunkt alle N Iterationen, sodass bei einem Abbruch weniger Fortschritt verloren geht. Die letzte Iteration schreibt immer. Dies ist unabhängig von Bestmodell-Snapshots, die bei einer Verbesserung durch die Auswertung entstehen.',
  'Keeps and registers a model file for every strict evaluation improvement instead of only the best model so far. Ties do not create extra models. Enabling it uses additional disk space.':
    'Behält und registriert eine Modelldatei für jede echte Verbesserung der Auswertung statt nur des bisher besten Modells. Gleichstände erzeugen keine zusätzlichen Modelle. Das Aktivieren belegt zusätzlichen Speicherplatz.',
  'Self-play games generated in each training iteration. More games per iteration produce more training positions and a better-filled replay buffer but lengthen the iteration. Multiplied by the iterations, this is the total self-play volume of the run.':
    'Selbstspielpartien, die in jeder Trainingsiteration erzeugt werden. Mehr Partien pro Iteration ergeben mehr Trainingsstellungen und einen besser gefüllten Trainingspuffer, verlängern aber die Iteration. Multipliziert mit den Iterationen ergibt das das gesamte Selbstspielvolumen des Laufs.',
  'Monte Carlo tree search simulations per move during self-play. More simulations search deeper and produce stronger, more reliable move targets, but every move costs more computation, so self-play games take longer to generate. The search also stops at the time limit, so the effective budget is whichever limit is reached first.':
    'Monte-Carlo-Baumsuche-Simulationen pro Zug im Selbstspiel. Mehr Simulationen suchen tiefer und erzeugen stärkere, verlässlichere Zugziele, kosten aber für jeden Zug mehr Rechenzeit, sodass Selbstspielpartien länger dauern. Die Suche endet auch am Zeitlimit, daher gilt das Limit, das zuerst erreicht wird.',
  'Wall-clock limit for the search on each move during self-play, in seconds. The search stops once this time has passed, after at least one simulation, even if the iteration budget is unused. It also becomes the default evaluation time limit when that is not set separately.':
    'Zeitlimit pro Zug im Selbstspiel in Sekunden. Die Suche endet, sobald diese Zeit vergangen ist (nach mindestens einer Simulation), auch wenn das Iterationsbudget ungenutzt ist. Es wird außerdem zum Standard-Zeitlimit der Auswertung, wenn dort kein eigenes gesetzt ist.',
  'How self-play games are executed. Automatic selects batched when the model runs on CUDA or OpenVINO, otherwise sequential. Sequential plays one game at a time. Batched plays up to the concurrency limit together and groups neural evaluations into batches, which is faster on a GPU. Batched and automatic also use the CPU search worker pool when it is enabled.':
    'Wie Selbstspielpartien ausgeführt werden. Automatisch wählt gebündelt, wenn das Modell auf CUDA oder OpenVINO läuft, sonst sequenziell. Sequenziell spielt eine Partie nach der anderen. Gebündelt spielt bis zur Nebenläufigkeitsgrenze gleichzeitig und fasst neuronale Auswertungen zu Stapeln zusammen, was auf einer GPU schneller ist. Gebündelt und Automatisch nutzen zudem den CPU-Suchprozess-Pool, wenn er aktiviert ist.',
  'Maximum number of self-play games running at once in batched mode, and an upper bound on how large a neural batch can grow. More concurrent games can raise throughput but use more memory. It has no effect in sequential mode and does not size the CPU search worker pool.':
    'Maximale Anzahl gleichzeitig laufender Selbstspielpartien im gebündelten Modus und Obergrenze für die Größe eines neuronalen Stapels. Mehr gleichzeitige Partien können den Durchsatz erhöhen, brauchen aber mehr Speicher. Im sequenziellen Modus ohne Wirkung; der CPU-Suchprozess-Pool wird dadurch nicht dimensioniert.',
  'Maximum plies in one self-play game. A game that reaches the limit ends unfinished and its final position is scored from the network’s value estimate rather than a win or loss. Higher limits let long games finish naturally; lower limits shorten iterations.':
    'Maximale Züge einer Selbstspielpartie. Eine Partie, die die Grenze erreicht, endet unvollendet, und ihre Endstellung wird aus der Wertschätzung des Netzes bewertet statt als Sieg oder Niederlage. Höhere Grenzen lassen lange Partien natürlich enden; niedrigere verkürzen die Iterationen.',
  'Sampling temperature used to pick self-play moves from the search policy. Higher values sample more randomly for variety; lower values play closer to the strongest move. After the first 16 plies the effective value drops to a quarter of this setting, with a minimum of 0.1. It affects self-play only: evaluation plays deterministically.':
    'Sampling-Temperatur für die Zugauswahl im Selbstspiel aus der Suchrichtlinie. Höhere Werte wählen zufälliger für mehr Vielfalt; niedrigere Werte spielen näher am stärksten Zug. Nach den ersten 16 Zügen sinkt der effektive Wert auf ein Viertel dieser Einstellung, mit einem Minimum von 0,1. Sie betrifft nur das Selbstspiel: Die Auswertung spielt deterministisch.',
  'Exploration constant of the search. Higher values favour less-visited moves and widen the search; lower values follow the network’s move priors more greedily. It affects self-play only; evaluation uses a fixed value.':
    'Explorationskonstante der Suche. Höhere Werte bevorzugen selten besuchte Züge und erweitern die Suche; niedrigere folgen den Zug-Priors des Netzes gieriger. Sie betrifft nur das Selbstspiel; die Auswertung nutzt einen festen Wert.',
  'Concentration of the Dirichlet noise added to the root move priors during self-play. Smaller values concentrate the noise on a few moves; larger values spread it more evenly. It only applies while the exploration noise fraction is above zero.':
    'Konzentration des Dirichlet-Rauschens, das im Selbstspiel auf die Wurzel-Priors der Züge gelegt wird. Kleinere Werte konzentrieren das Rauschen auf wenige Züge; größere verteilen es gleichmäßiger. Es wirkt nur, solange der Explorationsrausch-Anteil über null liegt.',
  'Share of the root move priors replaced by Dirichlet noise during self-play, which encourages trying alternatives to the network’s first choice. 0 disables the noise and 1 replaces the priors entirely. It affects self-play only; evaluation uses no noise.':
    'Anteil der Wurzel-Priors, der im Selbstspiel durch Dirichlet-Rauschen ersetzt wird, um Alternativen zur ersten Wahl des Netzes auszuprobieren. 0 deaktiviert das Rauschen, 1 ersetzt die Priors vollständig. Es betrifft nur das Selbstspiel; die Auswertung nutzt kein Rauschen.',
  'Maximum number of positions evaluated in one neural network forward pass. Larger batches improve device throughput but use more memory and can only be filled when enough positions are waiting, so the scheduler never waits to complete a batch. Also caps the batch OpenVINO compiles for self-play.':
    'Maximale Anzahl Stellungen, die in einem neuronalen Vorwärtsdurchlauf ausgewertet werden. Größere Stapel verbessern den Gerätedurchsatz, brauchen aber mehr Speicher und lassen sich nur füllen, wenn genug Stellungen warten; der Scheduler wartet nie, um einen Stapel zu füllen. Begrenzt zudem den Stapel, den OpenVINO fürs Selbstspiel kompiliert.',
  'Randomly rotates and reflects training positions together with their move targets so the network learns equivalent board orientations. Off trains on unmodified positions. It changes training samples only, not the game rules or the search budget, and requires a target that supports augmentation.':
    'Dreht und spiegelt Trainingsstellungen zufällig zusammen mit ihren Zugzielen, damit das Netz gleichwertige Brettausrichtungen erlernt. Aus trainiert mit unveränderten Stellungen. Es betrifft nur Trainingsbeispiele, nicht die Spielregeln oder das Suchbudget, und erfordert ein Ziel, das die Augmentation unterstützt.',
  'Number of Optuna trials to run. Each trial trains a full model with sampled hyperparameters, so a tuning job takes much longer than a single training run. When a saved study is continued, this is the additional budget on top of its completed trials.':
    'Anzahl der auszuführenden Optuna-Versuche. Jeder Versuch trainiert ein vollständiges Modell mit abgetasteten Hyperparametern, daher dauert ein Optimierungsauftrag viel länger als ein einzelner Trainingslauf. Wird eine gespeicherte Studie fortgesetzt, ist dies das zusätzliche Budget oberhalb ihrer abgeschlossenen Versuche.',
  'Name that identifies the Optuna study inside the job’s own study database. A new tuning job always starts a fresh database, so the same name alone does not continue an earlier study; continuing uses the saved job’s “Resume study” action. Must contain 1–100 characters.':
    'Name, der die Optuna-Studie innerhalb der eigenen Studien-Datenbank des Auftrags identifiziert. Ein neuer Optimierungsauftrag beginnt immer mit einer frischen Datenbank, daher setzt derselbe Name allein keine frühere Studie fort; zum Fortsetzen dient die Aktion „Studie fortsetzen“ des gespeicherten Auftrags. Muss 1–100 Zeichen enthalten.',
  'Seed for the Optuna sampler, separate from the model’s random seed. A fresh trial derives its model seed from the nested training seed (or this study seed when none is set), so the same inputs suggest the same parameters for a study with the same history, and a continued study keeps the saved seed.':
    'Startwert für den Optuna-Sampler, getrennt vom Zufallsstartwert des Modells. Ein frischer Versuch leitet seinen Modellstartwert vom verschachtelten Trainingsstartwert ab (oder von diesem Studienstartwert, wenn keiner gesetzt ist), sodass dieselben Eingaben bei gleicher Vorgeschichte dieselben Parameter vorschlagen, und eine fortgesetzte Studie behält den gespeicherten Startwert.',
  'JSON object describing which parameters Optuna samples and their ranges. Only tunable training parameters are allowed, such as channels, blocks, learning rate, weight decay, exploration, temperature, batch size, epochs, MCTS iterations and the Dirichlet values; evaluation and champion settings cannot be tuned. Supported distributions are float (optional log), int (optional positive step, log requires step 1) and categorical choices. The text is sent as the search space, so it must stay valid JSON.':
    'JSON-Objekt, das beschreibt, welche Parameter Optuna abtastet und in welchen Bereichen. Nur optimierbare Trainingsparameter sind erlaubt, etwa Kanäle, Blöcke, Lernrate, Gewichtsverfall, Exploration, Temperatur, Stapelgröße, Epochen, MCTS-Iterationen und die Dirichlet-Werte; Auswertungs- und Champion-Einstellungen können nicht optimiert werden. Unterstützte Verteilungen sind float (optional log), int (optional positiver step, log erfordert step 1) und kategorische Auswahl. Der Text wird als Suchraum übermittelt und muss daher gültiges JSON bleiben.',
  'Optional JSON for advanced study settings: “opponents” is a weighted list of fixed references ({opponent, weight}, or {opponent: "frozen_model", model_id, weight}) for a multi-opponent objective, “verification” re-evaluates the winning checkpoint with a held-out seed ({games, seed, opponent}), “sampler”/“pruner” set TPE and median-pruner startup, and “max_failures” bounds consecutive failing trials. Leave empty for the default single-opponent objective.':
    'Optionales JSON für erweiterte Studien-Einstellungen: „opponents“ ist eine gewichtete Liste fester Vergleichsgegner ({opponent, weight} oder {opponent: „frozen_model“, model_id, weight}) für ein Mehrgegner-Ziel, „verification“ bewertet den besten Prüfpunkt mit einem zurückgehaltenen Startwert erneut ({games, seed, opponent}), „sampler“/„pruner“ legen TPE- und Median-Pruner-Startphasen fest, und „max_failures“ begrenzt aufeinanderfolgende fehlgeschlagene Versuche. Leer lassen für das standardmäßige Einzelgegner-Ziel.',
  'Extra training iterations each checkpoint-based trial runs on top of the source checkpoint’s completed iteration. Every trial uses the same total, so hyperparameters are compared from one common state without modifying the source checkpoint.':
    'Zusätzliche Trainingsiterationen, die jeder prüfpunktbasierte Versuch oberhalb der abgeschlossenen Iteration des Quellprüfpunkts ausführt. Jeder Versuch nutzt dieselbe Gesamtzahl, sodass Hyperparameter von einem gemeinsamen Zustand aus verglichen werden, ohne den Quellprüfpunkt zu verändern.',
  'Optional study settings (JSON)': 'Optionale Studien-Einstellungen (JSON)',
  'Optional opponents, held-out verification, sampler/pruner startup and failure budget. Leave empty for the default single-opponent objective.':
    'Optionale Gegner, zurückgehaltene Überprüfung, Sampler-/Pruner-Startphase und Fehlerbudget. Leer lassen für das standardmäßige Einzelgegner-Ziel.',
  'Automatic hyperparameter updates': 'Automatische Hyperparameter-Aktualisierungen',
  'Learning rate candidates': 'Lernraten-Kandidaten',
  Off: 'Aus',
  HyperController: 'HyperController',
  'Decide every (evaluations)': 'Entscheidung alle (Evaluationen)',
  'Warm-up evaluations': 'Aufwärm-Evaluationen',
  'Minimum dwell (evaluations)': 'Minimale Verweildauer (Evaluationen)',
  'Observation history window': 'Beobachtungsfenster',
  'Minimum trials per candidate': 'Mindestversuche pro Kandidat',
  'Whether the job adjusts the learning rate online while it trains. Off keeps the configured rate fixed. HyperController adapts inside the same run from its evaluation scores; it is not Optuna, which searches across separate trials instead of one continuing trajectory. While it is enabled the controller owns the effective rate, the configured learning rate is the starting value and must be one of the candidates, and at least four evaluation games are required. Champion is supported: when a candidate is promoted the controller keeps its current effective rate, discards the learning it derived from the previous Champion and starts a new warm-up, so scores from before and after a promotion are never mixed.':
    'Ob der Auftrag die Lernrate während des Trainings online anpasst. Aus lässt die konfigurierte Rate fest. HyperController passt sie innerhalb desselben Laufs anhand der Auswertungsergebnisse an; das ist nicht Optuna, das über getrennte Versuche statt über eine fortlaufende Bahn sucht. Solange es aktiv ist, besitzt der Controller die effektive Rate, die konfigurierte Lernrate ist der Startwert und muss einer der Kandidaten sein, und mindestens vier Auswertungspartien sind erforderlich. Champion wird unterstützt: Wird ein Kandidat befördert, behält der Controller seine aktuelle effektive Rate, verwirft das aus dem vorherigen Champion Gelernte und startet eine neue Aufwärmphase, sodass Ergebnisse vor und nach einer Beförderung nie vermischt werden.',
  'Comma-separated learning rates the controller may choose between. Each must be between 1e-7 and 1 and all must be distinct, with 2–8 values. The configured learning rate must appear in the list. A small set keeps each decision cheap and the saved controller state bounded.':
    'Kommagetrennte Lernraten, zwischen denen der Controller wählen darf. Jede muss zwischen 1e-7 und 1 liegen und alle müssen verschieden sein, mit 2–8 Werten. Die konfigurierte Lernrate muss in der Liste vorkommen. Eine kleine Menge hält jede Entscheidung günstig und den gespeicherten Controller-Zustand begrenzt.',
  'Number of completed evaluations between controller decisions. 1 decides after every evaluation; higher values judge a longer interval before the rate can change.':
    'Anzahl abgeschlossener Auswertungen zwischen Controller-Entscheidungen. 1 entscheidet nach jeder Auswertung; höhere Werte beurteilen ein längeres Intervall, bevor sich die Rate ändern kann.',
  'Completed evaluations observed before the controller may change the learning rate. The configured starting rate is used during warm-up so the first decision is not made from too little evidence.':
    'Abgeschlossene Auswertungen, die beobachtet werden, bevor der Controller die Lernrate ändern darf. Während der Aufwärmphase wird die konfigurierte Startrate verwendet, damit die erste Entscheidung nicht aus zu wenig Evidenz getroffen wird.',
  'Minimum number of evaluations a newly selected learning rate is kept before it may change again. Prevents rapid oscillation when evaluation scores are noisy.':
    'Mindestanzahl an Auswertungen, die eine neu gewählte Lernrate beibehalten wird, bevor sie sich erneut ändern darf. Verhindert schnelles Oszillieren, wenn die Auswertungsergebnisse verrauscht sind.',
  'Number of recent observations the controller uses to predict the next score (1–3). A larger window conditions on more history but enlarges the learned state; 1 matches the reference configuration.':
    'Anzahl der jüngsten Beobachtungen, die der Controller zur Vorhersage des nächsten Ergebnisses nutzt (1–3). Ein größeres Fenster stützt sich auf mehr Vorgeschichte, vergrößert aber den gelernten Zustand; 1 entspricht der Referenzkonfiguration.',
  'Number of times every candidate must be observed in a given action-history context before the controller may exploit its predictions there. Coverage is per context, so a new context is always explored and a promising candidate cannot hide an unobserved alternative; the value must be at least 1.':
    'Anzahl, wie oft jeder Kandidat in einem gegebenen Aktionsverlauf-Kontext beobachtet werden muss, bevor der Controller dort seine Vorhersagen ausnutzen darf. Die Abdeckung ist pro Kontext, daher wird ein neuer Kontext immer erkundet und ein vielversprechender Kandidat kann keine unbeobachtete Alternative verbergen; der Wert muss mindestens 1 sein.',
  'Effective learning rate': 'Effektive Lernrate',
  'Used learning rate': 'Verwendete Lernrate',
  'Selected learning rate': 'Gewählte Lernrate',
  Observation: 'Beobachtung',
  Decision: 'Entscheidung',
  Changed: 'Geändert',
  Kept: 'Beibehalten',
  'Warm-up': 'Aufwärmphase',
  'Waiting for cadence': 'Warte auf Takt',
  'Minimum dwell': 'Minimale Verweildauer',
  'No observation': 'Keine Beobachtung',
  Fallback: 'Rückfall',
  'Champion promoted · warm-up restarted': 'Champion befördert · Aufwärmphase neu gestartet',
  'Promotion resets': 'Beförderungs-Resets',
  'Each Champion promotion keeps the current learning rate but restarts the controller’s warm-up; scores from before the promotion are not reused.':
    'Jede Champion-Beförderung behält die aktuelle Lernrate bei, startet aber die Aufwärmphase des Controllers neu; Ergebnisse von vor der Beförderung werden nicht wiederverwendet.',
  'Unavailable while tuning: the study varies the learning rate across trials.':
    'Während der Optimierung nicht verfügbar: Die Studie variiert die Lernrate über die Versuche hinweg.',
  'Automatic hyperparameter updates need at least 4 evaluation games.':
    'Automatische Hyperparameter-Aktualisierungen benötigen mindestens 4 Auswertungspartien.',
  'The learning rate must be one of the adaptation candidates.':
    'Die Lernrate muss einer der Anpassungskandidaten sein.',
  'Learning rate candidates must be decimal numbers separated by commas.':
    'Die Lernraten-Kandidaten müssen durch Kommas getrennte Dezimalzahlen sein.',
  'Learning rate candidates must be between 1e-7 and 1.':
    'Die Lernraten-Kandidaten müssen zwischen 1e-7 und 1 liegen.',
  'Learning rate candidates must contain between 2 and 8 distinct values.':
    'Die Lernraten-Kandidaten müssen zwischen 2 und 8 verschiedene Werte enthalten.',
  'Learning rate candidates must be at most 512 characters.':
    'Die Lernraten-Kandidaten dürfen höchstens 512 Zeichen lang sein.',
  'Learning rate candidates and the observation history window produce too many controller contexts; use fewer candidates or a smaller window.':
    'Lernraten-Kandidaten und das Beobachtungsverlaufsfenster erzeugen zu viele Controller-Kontexte; weniger Kandidaten oder ein kleineres Fenster verwenden.',
  'This worker does not support automatic hyperparameter updates. Choose a newer worker or disable them.':
    'Dieser Worker unterstützt keine automatischen Hyperparameter-Aktualisierungen. Wähle einen neueren Worker oder deaktiviere sie.',
  'Changing the learning rate on resume moves the controller’s effective rate; its learned state is kept.':
    'Eine Änderung der Lernrate beim Fortsetzen verschiebt die effektive Rate des Controllers; sein gelernter Zustand bleibt erhalten.',
};
export default translations;
