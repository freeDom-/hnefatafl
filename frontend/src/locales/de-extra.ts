const translations: Record<string, string> = {
  'Game options': 'Spieloptionen',
  'Read-only history': 'Schreibgeschützter Verlauf',
  'Loading history…': 'Verlauf wird geladen…',
  'Loading running games…': 'Laufende Partien werden geladen…',
  'Some running games could not be loaded.':
    'Einige laufende Partien konnten nicht geladen werden.',
  'No running games.': 'Keine laufenden Partien.',
  'Game information': 'Spielinformationen',
  Moves: 'Züge',
  Players: 'Spieler',
  'Select a move advisor in Game options to see analysis.':
    'Wähle in den Spieloptionen einen Zugberater für die Analyse.',

  Advanced: 'Fortgeschritten',
  Analytics: 'Analyse',
  'Show winning chances': 'Gewinnchancen anzeigen',
  'Custom move advisor': 'Eigener Zugberater',
  'Maximum search time (seconds)': 'Maximale Suchzeit (Sekunden)',
  'Analysis runs in the background. You can keep playing while the advisor searches.':
    'Die Analyse läuft im Hintergrund. Du kannst weiterspielen, während der Zugberater sucht.',
  'Saving analytics settings…': 'Analyseeinstellungen werden gespeichert…',

  'Game seeds are recorded per game and are excluded from this comparison.':
    'Zufalls-Seeds werden pro Partie gespeichert und für diesen Vergleich nicht unterschieden.',
  'Agent evaluations': 'Agentenbewertungen',
  'THROUGH THE AGENT’S EYES': 'MIT DEN AUGEN DES AGENTEN',
  'Winning chances': 'Gewinnchancen',
  'For the current player: {side}': 'Für die Seite am Zug: {side}',
  'Evaluating this position…': 'Stellung wird bewertet…',
  'The evaluation is unavailable. You can keep playing.':
    'Die Bewertung ist nicht verfügbar. Du kannst weiterspielen.',
  'Retry evaluation': 'Bewertung erneut versuchen',
  'No evaluation was recorded for this position.':
    'Für diese Stellung wurde keine Bewertung gespeichert.',
  'Move advisor': 'Zugberater',
  '{side} agent': 'Agent der {side}',
  '{agent} estimate for {side}': 'Einschätzung von {agent} für die {side}',
  'Heuristic estimate': 'Heuristische Einschätzung',
  'MCTS rollouts': 'MCTS-Simulationen',
  'Neural value head': 'Neuronale Stellungsbewertung',
  'Neural search estimate': 'Neuronale Sucheinschätzung',
  'Exact outcome': 'Feststehendes Ergebnis',
  Unavailable: 'Nicht verfügbar',
  '{count} simulations': '{count} Simulationen',
  'depth {depth}': 'Tiefe {depth}',
  'depth {completed} / {requested}': 'Tiefe {completed} / {requested}',
  'The time limit was reached before the configured depth finished.':
    'Das Zeitlimit wurde erreicht, bevor die eingestellte Suchtiefe abgeschlossen war.',
  'The model is no longer available. Saved evaluations remain in replay.':
    'Das Modell ist nicht mehr verfügbar. Gespeicherte Bewertungen bleiben in der Wiederholung erhalten.',
  'This agent could not evaluate the position.': 'Dieser Agent konnte die Stellung nicht bewerten.',
  'Suggested move: {from} → {to}': 'Zugvorschlag: {from} → {to}',
  'Agent estimates; draws count as half. These are not calibrated winning probabilities.':
    'Agentenschätzungen; Remis zählen halb. Dies sind keine kalibrierten Gewinnwahrscheinlichkeiten.',
  'Agent for move suggestions': 'Agent für Zugvorschläge',
  None: 'Keiner',
  'Highlights this agent’s best legal move on the board. The advisor does not play your moves.':
    'Markiert den besten legalen Zug dieses Agenten auf dem Brett. Der Berater führt keine Züge für dich aus.',
  'Suggested move from {from} to {to}': 'Zugvorschlag von {from} nach {to}',
  ', suggested source': ', empfohlener Ausgangspunkt',
  ', suggested target': ', empfohlenes Ziel',
  'Search wrapper': 'Suchverfahren',
  '{side} search wrapper': '{side}: Suchverfahren',
  'MCTS (default)': 'MCTS (Standard)',
  'None (policy only)': 'Keines (nur Strategieausgabe)',

  Notifications: 'Benachrichtigungen',
  'Dismiss notification': 'Benachrichtigung schließen',
  'Something went wrong. Please try again.': 'Etwas ist schiefgelaufen. Bitte versuche es erneut.',
  'Done.': 'Fertig.',
};
export default translations;
