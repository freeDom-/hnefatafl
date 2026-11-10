const translations: Record<string, string> = {
  'Learning metric': 'Lernmetrik',
  'Replay buffer capacity': 'Kapazität des Replay-Puffers',
  'Learning rate (used)': 'Lernrate (verwendet)',
  'Learning rate (selected)': 'Lernrate (ausgewählt)',
  'Training batch size': 'Trainings-Batchgröße',
  'Training epochs': 'Trainingsepochen',
  'Self-play MCTS iterations': 'MCTS-Iterationen im Selbstspiel',
  'Self-play MCTS time limit': 'MCTS-Zeitlimit im Selbstspiel',
  'Self-play games per iteration': 'Selbstspielpartien pro Iteration',
  'MCTS exploration constant': 'MCTS-Explorationskonstante',
  'Self-play temperature': 'Selbstspieltemperatur',
  'Dirichlet alpha': 'Dirichlet-Alpha',
  'Dirichlet fraction': 'Dirichlet-Anteil',
  'Maximum game plies': 'Maximale Halbzüge pro Partie',
  Samples: 'Trainingsbeispiele',
  Updates: 'Aktualisierungen',
  Epochs: 'Epochen',
  Simulations: 'Simulationen',
  Seconds: 'Sekunden',
  Coefficient: 'Koeffizient',
  Temperature: 'Temperatur',
  Fraction: 'Anteil',
  Plies: 'Halbzüge',
  'Selected job configuration; earlier iterations may differ.':
    'Konfiguration des ausgewählten Auftrags; frühere Iterationen können abweichen.',
  'Policy and value loss against self-play targets for each completed iteration.':
    'Strategie- und Wertverlust gegenüber Selbstspielzielen für jede abgeschlossene Iteration.',
  'Policy loss against self-play move targets.':
    'Strategieverlust gegenüber den Zugzielen aus dem Selbstspiel.',
  'Value loss against self-play outcome targets.':
    'Wertverlust gegenüber den Ergebniszielen aus dem Selbstspiel.',
  'Actual positions stored in replay after this iteration; distinct from configured capacity.':
    'Tatsächlich nach dieser Iteration im Replay gespeicherte Stellungen; die konfigurierte Kapazität ist davon unabhängig.',
  'Configured maximum replay positions, recorded for this training interval.':
    'Konfigurierte maximale Anzahl an Replay-Stellungen, aufgezeichnet für diesen Trainingsabschnitt.',
  'Optimizer updates performed during this iteration, not the cumulative update count.':
    'In dieser Iteration ausgeführte Optimierer-Aktualisierungen, keine kumulative Anzahl.',
  'Optimizer base rate used for this iteration. Schedule-Free warm-up rates and parameter update magnitudes differ.':
    'In dieser Iteration verwendete Basislernrate des Optimierers. Aufwärmlernraten von Schedule-Free und die Größe der Parameteränderungen weichen davon ab.',
  'Effective optimizer base rate selected after this iteration for the next training interval.':
    'Nach dieser Iteration ausgewählte effektive Basislernrate des Optimierers für den nächsten Trainingsabschnitt.',
  'Recorded training examples presented to the optimizer during this iteration.':
    'Aufgezeichnete Trainingsbeispiele, die dem Optimierer während dieser Iteration vorgelegt wurden.',
  'Configured replay samples per gradient batch.':
    'Konfigurierte Anzahl an Replay-Beispielen pro Gradienten-Batch.',
  'Configured optimization epochs per iteration.':
    'Konfigurierte Optimierungsepochen pro Iteration.',
  'Configured maximum simulations per move. Time limits and early termination can reduce actual work.':
    'Konfigurierte maximale Simulationen pro Zug. Zeitlimits und vorzeitiges Beenden können den tatsächlichen Aufwand verringern.',
  'Configured self-play search time limit per move, in seconds.':
    'Konfiguriertes Suchzeitlimit pro Selbstspielzug in Sekunden.',
  'Configured target self-play games per iteration, not a measured game count.':
    'Konfigurierte Zielanzahl an Selbstspielpartien pro Iteration, keine gemessene Partienanzahl.',
  'Configured optimizer regularization. Zero disables weight decay.':
    'Konfigurierte Optimierer-Regularisierung. Null deaktiviert die Gewichtsabnahme.',
  'Configured PUCT exploration constant for self-play.':
    'Konfigurierte PUCT-Explorationskonstante für das Selbstspiel.',
  'Configured self-play move-selection temperature.':
    'Konfigurierte Temperatur für die Zugauswahl im Selbstspiel.',
  'Configured root exploration-noise alpha.':
    'Konfiguriertes Alpha des Explorationsrauschens an der Suchwurzel.',
  'Configured fraction of root exploration noise.':
    'Konfigurierter Anteil des Explorationsrauschens an der Suchwurzel.',
  'Configured self-play game-length limit, not the measured game length.':
    'Konfiguriertes Längenlimit für Selbstspielpartien, keine gemessene Partienlänge.',
  'Use parameters': 'Parameter übernehmen',
  'Parameters copied from job {name}. Unrecorded inputs keep their current values. Starting creates a new job.':
    'Parameter aus Auftrag {name} übernommen. Nicht aufgezeichnete Eingaben behalten ihre aktuellen Werte. Beim Start entsteht ein neuer Auftrag.',
  'Invalid or unavailable settings kept safe values: {fields}. Review before starting.':
    'Ungültige oder nicht verfügbare Einstellungen behalten sichere Werte: {fields}. Vor dem Start prüfen.',
  'Study random seed': 'Zufallsstartwert der Studie',
  'Self-play games generated': 'Erzeugte Selbstspielpartien',
  'Job games': 'Selbstspielpartien dieses Auftrags',
  'Cumulative training games': 'Kumulative Trainingspartien',
  'Total self-play games': 'Selbstspielpartien insgesamt',
  '{count} games': '{count} Partien',
  '{count} game': '{count} Partie',
  'No valid recorded training parameters are available for this job.':
    'Für diesen Auftrag sind keine gültigen aufgezeichneten Trainingsparameter verfügbar.',
  'Study progress': 'Studienfortschritt',
  'Completed trials': 'Abgeschlossene Versuche',
  'Additional trial budget': 'Budget zusätzlicher Versuche',
  'Best trial score': 'Beste Versuchspunktzahl',
  'Current trial number': 'Aktuelle Versuchsnummer',
  'Trial history': 'Versuchsverlauf',
  Trial: 'Versuch',
  Parameters: 'Parameter',
  'Failed trials': 'Fehlgeschlagene Versuche',
  'Best trial': 'Bester Versuch',
  'Best checkpoint iteration': 'Iteration des besten Prüfpunkts',
  'Study sampler seed': 'Startwert des Studien-Samplers',
  'Source training seed': 'Startwert des Quelltrainings',
  'Trials are ranked by the score of their best evaluated checkpoint, not by the final iteration.':
    'Versuche werden nach der Punktzahl ihres besten ausgewerteten Prüfpunkts gereiht, nicht nach der letzten Iteration.',
  'Weighted opponent aggregate': 'Gewichtete Gegner-Zusammenfassung',
  Unfinished: 'Unvollendet',
  'Held-out verification': 'Zurückgehaltene Überprüfung',
  'Verification opponent': 'Überprüfungsgegner',
  'Verification score': 'Überprüfungspunktzahl',
  'Verification games': 'Überprüfungspartien',
  'Verification seed': 'Überprüfungsstartwert',
  'The verification stage uses a seed the trials never used, so it is an independent check of the winning checkpoint.':
    'Die Überprüfungsphase verwendet einen Startwert, den die Versuche nie nutzten, und ist damit eine unabhängige Prüfung des besten Prüfpunkts.',
  'Best configuration': 'Beste Konfiguration',
  'Winning trial configuration': 'Konfiguration des besten Versuchs',
  'Train with best parameters': 'Mit besten Parametern trainieren',
  'Continue winning trial': 'Besten Versuch fortsetzen',
  'Download best configuration': 'Beste Konfiguration herunterladen',
  'Additional iterations': 'Zusätzliche Iterationen',
  'Apply to existing training': 'Auf bestehendes Training anwenden',
  'Apply best parameters': 'Beste Parameter anwenden',
  'Best checkpoint score': 'Punktzahl des besten Prüfpunkts',
  'Final iteration score': 'Punktzahl der letzten Iteration',
  'Best iteration': 'Beste Iteration',
  'Tune this checkpoint': 'Diesen Prüfpunkt optimieren',
  'This job has no recorded best configuration.':
    'Für diesen Auftrag ist keine beste Konfiguration erfasst.',
  'This job has no recorded winning trial checkpoint.':
    'Für diesen Auftrag ist kein Prüfpunkt des besten Versuchs erfasst.',
  'Continuing the winning trial from its full checkpoint. A new training job has started.':
    'Der beste Versuch wird von seinem vollständigen Prüfpunkt fortgesetzt. Ein neuer Trainingsauftrag wurde gestartet.',
  'The selected training job cannot be resumed.':
    'Der ausgewählte Trainingsauftrag kann nicht fortgesetzt werden.',
  'Best parameters applied. The training job resumed from its own checkpoint.':
    'Beste Parameter angewendet. Der Trainingsauftrag wurde von seinem eigenen Prüfpunkt fortgesetzt.',
  'Best parameters loaded from job {name}. Starting creates a new training job.':
    'Beste Parameter aus Auftrag {name} geladen. Der Start erstellt einen neuen Trainingsauftrag.',
  'Tuning started from the saved checkpoint. Every trial begins from the same state.':
    'Optimierung vom gespeicherten Prüfpunkt gestartet. Jeder Versuch beginnt im selben Zustand.',
  'Close tuning settings': 'Optimierungseinstellungen schließen',
  'Additional tuning iterations per trial': 'Zusätzliche Optimierungsiterationen pro Versuch',
  'Each trial targets {target} total iterations ({completed} already completed).':
    'Jeder Versuch zielt auf {target} Iterationen insgesamt ({completed} bereits abgeschlossen).',
  'Every trial resumes this full checkpoint for the same additional budget. The source checkpoint is never modified.':
    'Jeder Versuch setzt diesen vollständigen Prüfpunkt mit demselben Zusatzbudget fort. Der Quellprüfpunkt wird nie verändert.',
  'The selected training job could not be found.':
    'Der ausgewählte Trainingsauftrag wurde nicht gefunden.',
  'Source checkpoint best score': 'Beste Punktzahl des Quellprüfpunkts',
  'Winning checkpoint source': 'Quelle des besten Prüfpunkts',
  'No candidate improved on the source checkpoint, so the reported objective is the inherited benchmark score and the selected checkpoint is the source best model.':
    'Kein Kandidat übertraf den Quellprüfpunkt, daher ist das gemeldete Ziel die übernommene Vergleichspunktzahl und der ausgewählte Prüfpunkt das beste Quellmodell.',
  'Source versus winning settings': 'Quell- und Siegereinstellungen',
  Setting: 'Einstellung',
  'Source checkpoint': 'Quellprüfpunkt',
  'Winning trial': 'Bester Versuch',
  Change: 'Änderung',
  Changed: 'Geändert',
  Unchanged: 'Unverändert',
  'Sort trials by': 'Versuche sortieren nach',
  'Trial number': 'Versuchsnummer',
  Duration: 'Dauer',
  'Filter by status': 'Nach Status filtern',
  Source: 'Quelle',
  inherited: 'übernommen',
  candidate: 'Kandidat',
  none: 'keine',
  'The winning trial is highlighted. A small evaluation sample does not establish a statistically significant improvement.':
    'Der beste Versuch ist hervorgehoben. Eine kleine Auswertungsstichprobe belegt keine statistisch signifikante Verbesserung.',

  'Could not load training jobs. Use Refresh to try again.':
    'Trainingsaufträge konnten nicht geladen werden. Versuche es mit Aktualisieren erneut.',
  'Workshop summary': 'Werkstattübersicht',
  'active jobs': 'aktive Aufträge',
  'registered models': 'registrierte Modelle',
  'New training': 'Neues Training',
  General: 'Allgemein',
  'Neural network and optimization': 'Neuronales Netz und Optimierung',
  'Self-play and MCTS': 'Selbstspiel und MCTS',
  'Loading training jobs…': 'Trainingsaufträge werden geladen…',
  'Hide job list': 'Auftragsliste ausblenden',
  'Show job list': 'Auftragsliste einblenden',
  'Training jobs': 'Trainingsaufträge',
  'Selected job': 'Ausgewählter Auftrag',
  'Select a training job': 'Trainingsauftrag auswählen',
  'Select a training job to inspect its history.':
    'Wähle einen Trainingsauftrag aus, um seinen Verlauf anzusehen.',
  'Find a training job': 'Trainingsauftrag suchen',
  'Search jobs': 'Aufträge suchen',
  'Job or model name': 'Auftrags- oder Modellname',
  'Sort jobs': 'Aufträge sortieren',
  'All statuses': 'Alle Status',
  'Active jobs': 'Aktive Aufträge',
  'Active first': 'Aktive zuerst',
  'Newest first': 'Neueste zuerst',
  'Oldest first': 'Älteste zuerst',
  '{shown} of {total} jobs': '{shown} von {total} Aufträgen',
  'No jobs match these filters.': 'Keine Aufträge entsprechen diesen Filtern.',
  'Outside current filters': 'Außerhalb der aktuellen Filter',
  'Selected training job': 'Ausgewählter Trainingsauftrag',
  Elapsed: 'Laufzeit',
  'Refresh job': 'Auftrag aktualisieren',
  'Training analysis': 'Trainingsanalyse',
  Overview: 'Übersicht',
  'Tuning results': 'Optimierungsergebnisse',
  Learning: 'Lernen',
  Evaluation: 'Auswertung',
  Performance: 'Leistung',
  Details: 'Details',
  'Iteration range': 'Iterationsbereich',
  'All iterations': 'Alle Iterationen',
  'Fit series': 'Datenreihe einpassen',
  'Recent {count} iterations': 'Letzte {count} Iterationen',
  'Recorded training progress': 'Erfasster Trainingsfortschritt',
  'Last completed iteration': 'Letzte abgeschlossene Iteration',
  'Latest evaluation': 'Letzte Auswertung',
  'No evaluation recorded yet.': 'Noch keine Auswertung erfasst.',
  'Iteration duration': 'Iterationsdauer',
  'Training failed. Inspect the error above and the diagnostic log in Details.':
    'Training fehlgeschlagen. Prüfe den Fehler oben und das Diagnoseprotokoll unter Details.',
  'Training stopped. Recorded iterations remain available; resume a full checkpoint to continue.':
    'Training gestoppt. Erfasste Iterationen bleiben verfügbar. Setze einen vollständigen Kontrollpunkt fort.',
  'Evaluation runs at the configured interval and after the final optimized iteration.':
    'Die Auswertung erfolgt im eingestellten Intervall und nach der letzten optimierten Iteration.',
  'Unfinished evaluation games earn zero points and are separate from rules draws. Review Evaluation for completion counts.':
    'Unvollendete Auswertungspartien erhalten null Punkte und zählen getrennt von Regelremis. Die Anzahl steht unter Auswertung.',
  'Loss measures fit to self-play targets; evaluation measures play against a specific opponent.':
    'Der Verlust misst die Anpassung an Selbstspielziele; die Auswertung misst das Spiel gegen einen bestimmten Gegner.',
  'Overview merges all recorded evaluation series. Compare individual settings in Evaluation.':
    'Die Übersicht fasst alle erfassten Auswertungsreihen zusammen. Vergleiche einzelne Einstellungen unter Auswertung.',
  'Checkpoint selection': 'Kontrollpunktauswahl',
  'Checkpoint selection metadata was not recorded by this worker.':
    'Dieser Worker hat keine Metadaten zur Kontrollpunktauswahl erfasst.',
  'Optimizer updates': 'Optimierungsschritte',
  'Missing metrics were not recorded; they are never interpreted as zero.':
    'Fehlende Messwerte wurden nicht erfasst und werden nie als null interpretiert.',
  'Effective evaluation settings were not recorded. Historical results remain available below.':
    'Die wirksamen Auswertungseinstellungen wurden nicht erfasst. Historische Ergebnisse bleiben unten verfügbar.',
  'A high score from a small sample does not establish absolute superiority. Confirm strength with larger, held-out tournaments.':
    'Eine hohe Punktzahl aus einer kleinen Stichprobe belegt keine absolute Überlegenheit. Prüfe die Stärke mit größeren, unabhängigen Turnieren.',
  'Complete evaluation settings': 'Vollständige Auswertungseinstellungen',
  'Raw progress and diagnostics': 'Fortschritts- und Diagnoserohdaten',
  'Checkpoint details': 'Kontrollpunktdetails',
  'Execution and remote-worker details': 'Ausführung und externe Worker',
  'Export and import': 'Export und Import',
  'Export an inactive job from its header to retain its configuration, history and available checkpoints. Use Import job to restore a portable job archive. Registered models are managed separately in Model collection.':
    'Exportiere einen inaktiven Auftrag über seine Kopfzeile, um Konfiguration, Verlauf und verfügbare Kontrollpunkte zu sichern. Stelle portable Archive mit Auftrag importieren wieder her. Registrierte Modelle werden getrennt in der Modellsammlung verwaltet.',
  Copy: 'Kopieren',
  Copied: 'Kopiert',
  'Copy failed. Select and copy the text manually.':
    'Kopieren fehlgeschlagen. Markiere und kopiere den Text manuell.',
  'Sequential phase timings': 'Zeiten aufeinanderfolgender Phasen',
  'Phase timings will appear when recorded by the worker.':
    'Phasenzeiten erscheinen, sobald der Worker sie erfasst.',
  'Self-play': 'Selbstspiel',
  Optimization: 'Optimierung',
  'Performance metrics were not recorded by this worker.':
    'Dieser Worker hat keine Leistungswerte erfasst.',
  'Performance metric': 'Leistungskennzahl',
  'Simulation completion ratio': 'Anteil abgeschlossener Simulationen',
  'GPU and inference': 'GPU und Inferenz',
  'Overlapping activity counters': 'Überlappende Aktivitätszeiten',
  'Historical settings': 'Historische Einstellungen',
  Loss: 'Verlust',
  'Score (%)': 'Punktzahl (%)',
  Positions: 'Stellungen',
  'No recorded observations': 'Keine erfassten Beobachtungen',
  'Point to the chart or use arrow keys for exact values.':
    'Zeige auf das Diagramm oder verwende die Pfeiltasten für genaue Werte.',
  'Use left and right arrow keys to inspect iterations. Exact values are available in the data table.':
    'Prüfe Iterationen mit der linken und rechten Pfeiltaste. Genaue Werte stehen in der Datentabelle.',
  'Opponent identity': 'Gegneridentität',
  'self play': 'Selbstspiel',
  initializing: 'Initialisierung',
  optimization: 'Optimierung',
  evaluation: 'Auswertung',
  'iteration complete': 'Iteration abgeschlossen',
  preparing: 'Vorbereitung',
  'initializing study': 'Studie wird initialisiert',
  'preparing trial': 'Versuch wird vorbereitet',
  'between trials': 'Zwischen den Versuchen',
  'finalizing study': 'Studie wird abgeschlossen',
  checkpoint: 'Kontrollpunkt',
  finalizing: 'Abschluss',
  pruned: 'Verworfen',
  starting: 'Start',
  'loading checkpoint': 'Kontrollpunkt wird geladen',
  'initializing model': 'Modell wird initialisiert',
  'restoring state': 'Trainingszustand wird wiederhergestellt',
  'initializing inference': 'Inferenz wird vorbereitet',
  'Evaluation series': 'Auswertungsreihe',
  'All evaluation series': 'Alle Auswertungsreihen',
  'Latest series': 'Neueste Reihe',
  'Latest evaluation settings': 'Neueste Auswertungseinstellungen',
  'Merged evaluation series': 'Zusammengefasste Auswertungsreihen',
  '{count} series': '{count} Reihen',
};
export default translations;
