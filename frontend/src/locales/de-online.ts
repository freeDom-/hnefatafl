const online: Record<string, string> = {
  'Each accepted move renews the allowance.': 'Jeder akzeptierte Zug erneuert das Zeitlimit.',
  'Time control': 'Zeitkontrolle',
  'Per move': 'Pro Zug',
  'Total per player': 'Gesamtzeit pro Spieler',
  'Total time per player': 'Gesamtzeit pro Spieler',
  'Total time per player: {duration}': 'Gesamtzeit pro Spieler: {duration}',
  'Increment per move (seconds)': 'Zeitbonus pro Zug (Sekunden)',
  'Increment: {seconds} seconds': 'Zeitbonus: {seconds} Sekunden',
  'Enter an increment in whole seconds from 0 to 1209600.':
    'Gib einen Zeitbonus in ganzen Sekunden von 0 bis 1209600 ein.',
  'Each player has their own total clock. The increment is added to your clock after you move.':
    'Jeder Spieler hat eine eigene Gesamtzeit. Nach deinem Zug wird der Zeitbonus deiner Uhr hinzugefügt.',
  'The first turn uses the base limit. Each later turn resets to the base limit plus the increment.':
    'Für den ersten Zug gilt das Grundlimit. Jeder weitere Zug erhält das Grundlimit plus Zeitbonus.',
  'Attackers remaining time': 'Restzeit der Angreifer',
  'Defenders remaining time': 'Restzeit der Verteidiger',
  'Time expired. Finalizing result…': 'Zeit abgelaufen. Ergebnis wird ermittelt…',
  'This retained request uses exactly {duration}. Retrying keeps that value.':
    'Diese gespeicherte Anfrage verwendet genau {duration}. Beim erneuten Senden bleibt dieser Wert erhalten.',
  Online: 'Online',
  Concede: 'Aufgeben',
  'Log out': 'Abmelden',
  'Logging out…': 'Abmelden…',
  'Online lobby': 'Online-Lobby',
  'Online game': 'Online-Partie',
  'Play another person. Every move is confirmed by the server.':
    'Spiele gegen einen Menschen. Der Server bestätigt jeden Zug.',
  'Single game': 'Einzelpartie',
  'Two-game match — simultaneous': 'Zwei-Partien-Match — gleichzeitig',
  'Two-game match — sequential': 'Zwei-Partien-Match — nacheinander',
  'Random at join': 'Zufällig beim Beitritt',
  Waiting: 'Wartend',
  Active: 'Aktiv',
  Completed: 'Abgeschlossen',
  Cancelled: 'Abgesagt',
  Voided: 'Annulliert',
  Unassigned: 'Noch nicht zugewiesen',
  'Game {leg}': 'Partie {leg}',
  'Time per move: {seconds} seconds': 'Zeit pro Zug: {seconds} Sekunden',
  'Open game': 'Partie öffnen',
  'Create an online game': 'Online-Partie erstellen',
  'Creator side': 'Seite des Erstellers',
  'Match mode': 'Match-Modus',
  'Seconds per move': 'Sekunden pro Zug',
  '60–1209600 seconds. Both players receive a fresh allowance after each accepted move.':
    '60–1209600 Sekunden. Nach jedem angenommenen Zug beginnt die Zugfrist neu.',
  'Use a whole number of seconds from 60 to 1209600.':
    'Gib eine ganze Zahl zwischen 60 und 1209600 Sekunden ein.',
  'Rulesets unavailable. Refresh the page to recover.':
    'Regelwerke nicht verfügbar. Lade die Seite neu.',
  'Request retained. Check My games before deliberately retrying the same request. A conflict can mean it already committed.':
    'Anfrage gespeichert. Prüfe „Meine Partien“, bevor du dieselbe Anfrage bewusst wiederholst. Ein Konflikt kann bedeuten, dass sie bereits gespeichert wurde.',
  'Retry retained request': 'Gespeicherte Anfrage erneut senden',
  'Create game': 'Partie erstellen',
  'Discard request after checking My games': 'Anfrage nach Prüfung von „Meine Partien“ verwerfen',
  'Disconnecting, closing the browser or logging out does not pause clocks.':
    'Verbindungsabbruch, Schließen des Browsers und Abmelden halten die Uhr nicht an.',
  'My games': 'Meine Partien',
  'Visible games': 'Sichtbare Partien',
  'All games': 'Alle Partien',
  'No games on this page.': 'Keine Partien auf dieser Seite.',
  'Game pages': 'Partieseiten',
  'Page {page}': 'Seite {page}',
  'Move deadline': 'Zugfrist',
  'Awaiting adjudication. The server still reports this game as Active.':
    'Entscheidung ausstehend. Der Server meldet diese Partie weiterhin als aktiv.',
  'Approximately {seconds} seconds remaining': 'Ungefähr {seconds} Sekunden verbleibend',
  'Countdown uses your device clock. Only the server decides deadlines and results.':
    'Der Countdown verwendet deine Geräteuhr. Nur der Server entscheidet über Fristen und Ergebnisse.',
  'No winner': 'Kein Gewinner',
  'Back to online lobby': 'Zurück zur Online-Lobby',
  'Rules version': 'Regelversion',
  'Match games': 'Partien des Matches',
  'Version {version}': 'Version {version}',
  'Historical replay · read only': 'Historische Wiedergabe · nur Ansicht',
  'Replay move {ply}': 'Wiedergabezug {ply}',
  'Select your piece, then an empty destination. The server checks the move.':
    'Wähle deine Figur und dann ein leeres Zielfeld. Der Server prüft den Zug.',
  'Waiting for your turn or server confirmation.':
    'Warte auf deinen Zug oder die Serverbestätigung.',
  'You are viewing this game. Only the seated player whose turn it is may act.':
    'Du siehst diese Partie an. Nur der Spieler am Zug darf handeln.',
  'View replay': 'Wiedergabe ansehen',
  'Previous move': 'Vorheriger Zug',
  'Next move': 'Nächster Zug',
  'Return to live board': 'Zum aktuellen Brett',
  'New moves are available on the live board.': 'Auf dem aktuellen Brett sind neue Züge verfügbar.',
  'Replay unavailable.': 'Wiedergabe nicht verfügbar.',
  'Players and result': 'Spieler und Ergebnis',
  You: 'Du',
  'Waiting for a second player. Random sides are assigned when they join. To change settings, cancel and create again.':
    'Warte auf einen zweiten Spieler. Zufällige Seiten werden beim Beitritt vergeben. Zum Ändern der Einstellungen absagen und neu erstellen.',
  'Join game': 'Partie beitreten',
  'Cancel waiting game': 'Wartende Partie absagen',
  'Confirm concession': 'Aufgabe bestätigen',
  'Concede this game? This ends your game if the server accepts it.':
    'Diese Partie aufgeben? Bei Annahme durch den Server endet die Partie.',
  'Keep playing': 'Weiterspielen',
  'Waiting for server confirmation…': 'Warte auf Serverbestätigung…',
  'Accepted moves': 'Angenommene Züge',
  'Loading online games…': 'Online-Partien werden geladen…',
  'Stale state. Reconnecting…': 'Veralteter Stand. Verbindung wird wiederhergestellt…',
  'Connected · refreshes automatically': 'Verbunden · automatische Aktualisierung',
  'Connection lost. Reconnecting…': 'Verbindung verloren. Verbindung wird wiederhergestellt…',
  'New online games and joining are currently disabled. Existing seats remain available.':
    'Neue Online-Partien und Beitritte sind derzeit deaktiviert. Bereits belegte Plätze bleiben verfügbar.',
  'Creating and joining require an active, eligible, verified account. Existing seats remain available.':
    'Erstellen und Beitreten erfordern ein aktives, berechtigtes, verifiziertes Konto. Bereits belegte Plätze bleiben verfügbar.',
  'The server denied this action. Check eligibility, your seat and whose turn it is.':
    'Der Server hat die Aktion abgelehnt. Prüfe Berechtigung, Platz und Zugrecht.',
  'State changed or adjudication is paused. Reloaded without repeating your action.':
    'Der Stand hat sich geändert oder die Zeitentscheidung ist pausiert. Ohne Wiederholung deiner Aktion neu geladen.',
  'Check the request values.': 'Prüfe die Eingabewerte.',
  'Creation limit reached. Cancel a waiting game or wait before deliberately retrying.':
    'Erstellungslimit erreicht. Sage eine wartende Partie ab oder warte, bevor du bewusst erneut versuchst.',
  'Online service unavailable. The last confirmed state is retained.':
    'Online-Dienst nicht verfügbar. Der zuletzt bestätigte Stand bleibt erhalten.',
  'Request failed. Review the refreshed state before trying again.':
    'Anfrage fehlgeschlagen. Prüfe den aktualisierten Stand vor einem neuen Versuch.',
  'The outcome is uncertain. Review the refreshed state before trying again.':
    'Das Ergebnis ist ungewiss. Prüfe den aktualisierten Stand vor einem neuen Versuch.',
  'Action confirmed by the server.': 'Aktion vom Server bestätigt.',
  'The server finalized the deadline. Your requested action was not accepted.':
    'Der Server hat die Zeitüberschreitung entschieden. Deine angefragte Aktion wurde nicht angenommen.',
  'Game created.': 'Partie erstellt.',
  'Player joined.': 'Spieler beigetreten.',
  'Waiting game cancelled.': 'Wartende Partie abgesagt.',
  'Move accepted.': 'Zug angenommen.',
  'Concession accepted.': 'Aufgabe angenommen.',
  'Deadline finalized.': 'Zeitüberschreitung entschieden.',
  'No state change.': 'Keine Zustandsänderung.',
  'Game voided.': 'Partie annulliert.',
  'The server did not accept the requested action. Review the refreshed state.':
    'Der Server hat die angefragte Aktion nicht angenommen. Prüfe den aktualisierten Stand.',
  joined: 'Beigetreten',
  cancelled: 'Abgesagt',
  moved: 'Zug angenommen',
  conceded: 'Aufgegeben',
  timeout: 'Zeitüberschreitung',
  concession: 'Aufgabe',
  abandonment: 'Zeitüberschreitung',
};
export default online;
