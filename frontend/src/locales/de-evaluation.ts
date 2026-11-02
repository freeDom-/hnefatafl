const translations: Record<string, string> = {
  Evaluation: 'Auswertung',
  'Evaluation execution': 'Auswertungsmodus',
  'Evaluation inference backend': 'Inferenzsystem der Auswertung',
  'Use training inference backend': 'Inferenzsystem des Trainings verwenden',
  'Concurrent evaluation games': 'Gleichzeitige Auswertungspartien',
  'Evaluation inference batch size': 'Inferenz-Stapelgröße der Auswertung',
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

  // Field help: the shared evaluation-parameter tooltips.
  'Opponent every evaluation compares the candidate against. Shield bearer is a fast one-ply heuristic benchmark for early training; Shield maiden is a stronger rules-aware heuristic with low overhead; Jarl uses alpha-beta search and is the most expensive. Champion compares against the current best model and is the only opponent that can promote the candidate. Changing it starts a new comparison series.':
    'Gegner, gegen den jede Auswertung den Kandidaten vergleicht. Schildträger ist ein schneller Ein-Zug-Heuristikvergleich für frühes Training; Schildmaid ist eine stärkere regelbewusste Heuristik mit geringem Aufwand; Jarl nutzt Alpha-Beta-Suche und ist am teuersten. Champion vergleicht gegen das aktuelle beste Modell und ist der einzige Gegner, der den Kandidaten befördern kann. Eine Änderung startet eine neue Vergleichsreihe.',
  'How evaluation games are executed. Automatic uses batched evaluation when the model runs on CUDA or the inference backend is OpenVINO, otherwise sequential. Batched plays games concurrently and groups neural evaluations; sequential runs one game at a time. It affects speed only, never the evaluation result.':
    'Wie Auswertungspartien ausgeführt werden. Automatisch nutzt gebündelte Auswertung, wenn das Modell auf CUDA läuft oder das Inferenzsystem OpenVINO ist, sonst sequenziell. Gebündelt spielt Partien gleichzeitig und fasst neuronale Auswertungen zusammen; sequenziell spielt eine Partie nach der anderen. Es betrifft nur die Geschwindigkeit, nie das Auswertungsergebnis.',
  'Inference backend used for evaluation games. PyTorch always evaluates with PyTorch. Use training inference backend follows the job’s neural inference backend, so it only changes anything when that backend is OpenVINO.':
    'Inferenzsystem für Auswertungspartien. PyTorch wertet immer mit PyTorch aus. „Inferenzsystem des Trainings verwenden“ folgt dem neuronalen Inferenzsystem des Auftrags und ändert daher nur etwas, wenn dieses OpenVINO ist.',
  'Runs evaluation every N iterations. The final iteration is always evaluated regardless of this value. More frequent evaluation tracks progress closely but spends more time not generating training data, and it is independent of the checkpoint frequency.':
    'Führt die Auswertung alle N Iterationen aus. Die letzte Iteration wird unabhängig von diesem Wert immer ausgewertet. Häufigere Auswertung verfolgt den Fortschritt genau, kostet aber Zeit, in der keine Trainingsdaten entstehen, und ist unabhängig vom Speicherintervall.',
  'Number of games in one evaluation. It must be even so the candidate plays both sides, and each pair of games shares a seed with the sides swapped. More games give a more reliable score and are needed to decide champion promotion.':
    'Anzahl der Partien einer Auswertung. Sie muss gerade sein, damit der Kandidat beide Seiten spielt, und jedes Partiepaar teilt einen Startwert mit getauschten Seiten. Mehr Partien ergeben ein verlässlicheres Ergebnis und sind nötig, um eine Championbeförderung zu entscheiden.',
  'Maximum number of evaluation games played at once in batched mode, and an upper bound on how many CPU search workers the phase uses. More concurrency can speed evaluation up but uses more memory; sequential mode ignores it.':
    'Maximale Anzahl gleichzeitig gespielter Auswertungspartien im gebündelten Modus und Obergrenze für die Zahl der CPU-Suchprozesse der Phase. Mehr Nebenläufigkeit kann die Auswertung beschleunigen, braucht aber mehr Speicher; der sequenzielle Modus ignoriert sie.',
  'Maximum positions per neural forward pass during evaluation. Larger batches improve throughput but use more memory. It has no effect in the sequential path.':
    'Maximale Anzahl Stellungen pro neuronalem Vorwärtsdurchlauf bei der Auswertung. Größere Stapel verbessern den Durchsatz, brauchen aber mehr Speicher. Im sequenziellen Pfad ohne Wirkung.',
  'Ply limit for one evaluation game. A game that reaches it counts as unfinished: it contributes no score but still counts in the denominator, which lowers the score, and it makes champion promotion inconclusive for that round.':
    'Zuggrenze einer Auswertungspartie. Eine Partie, die sie erreicht, gilt als unvollendet: Sie trägt kein Ergebnis bei, zählt aber im Nenner mit, was das Ergebnis senkt, und macht die Championbeförderung für diese Runde ergebnislos.',
  'MCTS simulations per move for the neural candidate during evaluation, and for the champion when that is the opponent. Higher values make evaluation stronger and slower. It is not used by the heuristic or Jarl opponents.':
    'MCTS-Simulationen pro Zug für den neuronalen Kandidaten bei der Auswertung und für den Champion, wenn dieser der Gegner ist. Höhere Werte machen die Auswertung stärker und langsamer. Von den Heuristik- und Jarl-Gegnern wird der Wert nicht genutzt.',
  'Wall-clock search limit per move for evaluation, in seconds. It defaults to the self-play search time limit when not set. It applies to the neural candidate and to the Shield bearer and Shield maiden opponents; Jarl uses its own limit instead.':
    'Zeitlimit pro Zug für die Auswertung in Sekunden. Es entspricht standardmäßig dem Suchzeitlimit des Selbstspiels, wenn kein eigenes gesetzt ist. Es gilt für den neuronalen Kandidaten und für die Gegner Schildträger und Schildmaid; Jarl nutzt stattdessen sein eigenes Limit.',
  'Alpha-beta search depth for the Jarl opponent. Higher depth makes Jarl stronger and slower. Only used while Jarl is the selected opponent.':
    'Suchtiefe der Alpha-Beta-Suche für den Jarl-Gegner. Höhere Tiefe macht Jarl stärker und langsamer. Wird nur genutzt, solange Jarl der gewählte Gegner ist.',
  'Wall-clock limit per move for the Jarl opponent’s alpha-beta search, in seconds. Jarl ignores the general evaluation time limit. Only used while Jarl is the selected opponent.':
    'Zeitlimit pro Zug für die Alpha-Beta-Suche des Jarl-Gegners in Sekunden. Jarl ignoriert das allgemeine Auswertungs-Zeitlimit. Wird nur genutzt, solange Jarl der gewählte Gegner ist.',
  'Confidence level of the one-sided bound used to decide champion promotion. Higher values make the bound stricter, so a candidate needs a clearer lead over the current champion to be promoted.':
    'Konfidenzniveau der einseitigen Schranke, die über die Championbeförderung entscheidet. Höhere Werte machen die Schranke strenger, sodass ein Kandidat einen klareren Vorsprung gegenüber dem aktuellen Champion braucht.',
  'Minimum number of complete side-swapped game pairs required before promotion can be decided. Fewer pairs leave the result inconclusive. The evaluation game count must be at least twice this value.':
    'Mindestanzahl vollständiger Partiepaare mit getauschten Seiten, bevor eine Beförderung entschieden werden kann. Weniger Paare lassen das Ergebnis ergebnislos. Die Zahl der Auswertungspartien muss mindestens das Doppelte betragen.',
  'Extra score above 50% that the candidate’s lower confidence bound must exceed for promotion. 0 requires the bound to be strictly above 50%; higher values demand a clearer improvement.':
    'Zusätzlicher Ergebnisanteil über 50 %, den die untere Konfidenzgrenze des Kandidaten für eine Beförderung überschreiten muss. 0 verlangt eine Schranke strikt über 50 %; höhere Werte fordern eine klarere Verbesserung.',
};
export default translations;
