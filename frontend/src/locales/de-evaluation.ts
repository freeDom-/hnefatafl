const translations: Record<string, string> = {
  Evaluation: 'Auswertung',
  'Evaluation opponent': 'Auswertungsgegner',
  Champion: 'Champion',
  'Previous model': 'Vorgängermodell',
  'Evaluation strategy': 'Auswertungsstrategie',
  'Evaluation search time limit (seconds)': 'Suchzeitlimit der Auswertung (Sekunden)',
  'Jarl evaluation depth': 'Jarl-Suchtiefe der Auswertung',
  'Jarl search time limit (seconds)': 'Jarl-Suchzeitlimit (Sekunden)',
  'Champion confidence level': 'Konfidenzniveau für den Champion',
  'Minimum complete evaluation pairs': 'Mindestanzahl vollständiger Auswertungspaare',
  'Required champion improvement': 'Erforderliche Verbesserung gegenüber dem Champion',
  'Only the selected opponent is evaluated. The final iteration is also evaluated; checkpoint frequency is independent.':
    'Nur der gewählte Gegner wird ausgewertet. Die letzte Iteration wird ebenfalls ausgewertet; das Speicherintervall ist unabhängig davon.',
  'Optuna uses the fixed Shield bearer objective for comparable trials.':
    'Optuna verwendet den festen Schildträger-Vergleich für vergleichbare Versuche.',
  'Champion promotion requires complete side-swapped pairs and a confidence bound above 50% plus the required improvement. Small leads may be inconclusive.':
    'Ein neuer Champion benötigt vollständige Partienpaare mit Seitenwechsel und eine Konfidenzgrenze über 50 % zuzüglich der erforderlichen Verbesserung. Kleine Vorsprünge können ohne eindeutiges Ergebnis bleiben.',
  'Jarl uses alpha-beta search. Depth and deadlines can make evaluation expensive.':
    'Jarl verwendet Alpha-Beta-Suche. Suchtiefe und Zeitlimits können die Auswertung aufwendig machen.',
  'Shield bearer is a heuristic benchmark for early training. Tied scores keep the earlier best checkpoint.':
    'Der Schildträger ist ein heuristischer Vergleich für frühes Training. Bei Gleichstand bleibt der bisher beste Prüfpunkt erhalten.',
  'This game budget is below the minimum pair count; promotion will remain inconclusive.':
    'Dieses Partienbudget liegt unter der Mindestanzahl an Paaren; ein Championwechsel ist damit nicht möglich.',
  'Latest training iteration': 'Letzte Trainingsiteration',
  'Accepted champion iteration': 'Iteration des akzeptierten Champions',
  'Provisional incumbent': 'Vorläufiger Titelhalter',
  'Last evaluation iteration': 'Letzte Auswertungsiteration',
  'Attacker-side score': 'Ergebnis als Angreifer',
  'Defender-side score': 'Ergebnis als Verteidiger',
  'Rules draws': 'Regelbedingte Remis',
  'Evaluation duration (seconds)': 'Auswertungsdauer (Sekunden)',
  'Effective evaluation: {games} games, {plies} ply limit, {visits} neural simulations, {seconds}s per neural move.':
    'Effektive Auswertung: {games} Partien, Limit {plies} Halbzüge, {visits} neuronale Simulationen, {seconds}s pro neuronalen Zug.',
  'Opponent deadline: {seconds}s.': 'Zeitlimit des Gegners: {seconds}s.',
  'Jarl depth: {depth}.': 'Jarl-Suchtiefe: {depth}.',
  'This benchmark is near saturation and provides little discrimination. Consider a stronger evaluation opponent.':
    'Dieser Vergleich ist nahezu gesättigt und unterscheidet Modelle kaum noch. Ein stärkerer Auswertungsgegner kann helfen.',
  'Candidate promoted to champion.': 'Kandidat als neuer Champion akzeptiert.',
  'Champion retained; improvement was not established.':
    'Champion beibehalten; eine Verbesserung wurde nicht nachgewiesen.',
  'Best fixed-benchmark checkpoints': 'Beste Prüfpunkte je festem Vergleich',
  'Shield bearer · historical settings': 'Schildträger · historische Einstellungen',
  'Evaluation series use separate opponent configurations and champion incumbents. Missing observations are not zero scores.':
    'Auswertungsreihen trennen unterschiedliche Gegnerkonfigurationen und Champions. Fehlende Messungen sind keine Nullergebnisse.',
  'Champion promotion': 'Championwechsel',
  'Series {number}': 'Reihe {number}',
  'Effective evaluation settings': 'Effektive Auswertungseinstellungen',
  'Candidate versus champion score': 'Ergebnis Kandidat gegen Champion',
  'Champion score lower bound': 'Untere Konfidenzgrenze des Ergebnisses',
  'Required score': 'Erforderliches Ergebnis',
  Promoted: 'Akzeptiert',
  Retained: 'Beibehalten',
  'Attacker wins': 'Angreifersiege',
  'Defender wins': 'Verteidigersiege',
  'Requested MCTS simulations': 'Angeforderte MCTS-Simulationen',
  'Completed MCTS simulations': 'Abgeschlossene MCTS-Simulationen',
  'Immediate-win shortcuts': 'Abkürzungen durch direkte Gewinnzüge',
  'Search deadline cutoffs': 'Suchabbrüche am Zeitlimit',
};
export default translations;
