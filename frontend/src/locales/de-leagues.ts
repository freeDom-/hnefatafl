const leagues: Record<string, string> = {
  'Current season': 'Laufende Saison',
  'Current season · {season}': 'Laufende Saison · {season}',
  'Archived season · {season}': 'Archivierte Saison · {season}',
  'Standings season': 'Saison für Tabelle und Partien',
  'Personal progression': 'Persönlicher Fortschritt',
  'Play a league game to begin your seasonal progress.':
    'Spiele eine Ligapartie, um deinen Saisonfortschritt zu beginnen.',
  'You have no eligible league results in this ruleset and season.':
    'Du hast keine gewerteten Ligaergebnisse in diesem Regelwerk und dieser Saison.',
  'Personal progression will return after the season rebuild completes.':
    'Der persönliche Fortschritt ist nach der Neuberechnung der Saison wieder verfügbar.',
  'Promoted to {tier}': 'Aufstieg nach {tier}',
  'Demoted to {tier}': 'Abstieg nach {tier}',
  '{tier} · {points} of {minimum} points': '{tier} · {points} von {minimum} Punkten',
  'Qualification · {played} of {required} eligible results':
    'Qualifikation · {played} von {required} gewerteten Ergebnissen',
  'Highest league tier reached.': 'Höchste Ligastufe erreicht.',
  'Current rank #{rank}': 'Aktueller Rang {rank}',
  'Season rank #{rank}': 'Saisonrang {rank}',
  'Qualified for standings': 'Für die Tabelle qualifiziert',
  Unranked: 'Ohne Rang',
  Points: 'Punkte',
  Tier: 'Ligastufe',
  'Season scoring · {win} win, {draw} draw, {loss} loss':
    'Saisonwertung · {win} Sieg, {draw} Unentschieden, {loss} Niederlage',
  Matchmaking: 'Spielersuche',
  'Waiting for an opponent': 'Warte auf einen Gegner',
  'Your queue entry expired.': 'Dein Warteschlangeneintrag ist abgelaufen.',
  'Your queue entry was cancelled.': 'Dein Warteschlangeneintrag wurde abgebrochen.',
  'You are not in the league queue.': 'Du bist nicht in der Liga-Warteschlange.',
  'League game ready': 'Ligapartie bereit',
  'Join queue': 'Warteschlange beitreten',
  'Cancel queue': 'Warteschlange verlassen',
  'Open league game': 'Ligapartie öffnen',
  'Refresh queue status': 'Warteschlangenstatus aktualisieren',
  'Refresh leagues': 'Ligadaten aktualisieren',
  'A previous join is unconfirmed. Refresh to check the queue.':
    'Ein vorheriger Beitritt ist noch unbestätigt. Aktualisiere den Warteschlangenstatus.',
  'Retry previous join': 'Vorherigen Beitritt wiederholen',
  'Queue status may be out of date.': 'Der Warteschlangenstatus kann veraltet sein.',
  'Displayed league data may be out of date.': 'Die angezeigten Ligadaten können veraltet sein.',
  'Joining…': 'Beitritt läuft…',
  'Cancelling…': 'Wird abgebrochen…',
  'League matchmaking is paused.': 'Die Liga-Spielersuche ist pausiert.',
  'Online matchmaking is currently unavailable.':
    'Die Online-Spielersuche ist derzeit nicht verfügbar.',
  'Online clocks are paused. League matchmaking will resume when clocks are enabled.':
    'Die Online-Schachuhren sind pausiert. Die Liga-Spielersuche wird fortgesetzt, sobald sie wieder aktiv sind.',
  'Each player has {minutes} minutes total and gains {increment} seconds after each move.':
    'Pro Person stehen insgesamt {minutes} Minuten zur Verfügung. Nach jedem Zug kommen {increment} Sekunden hinzu.',
  'Both players must be within each other’s rating window. It starts at ±{initial} Elo and widens by {step} every {interval} seconds, up to ±{maximum} Elo.':
    'Beide Spieler müssen im Wertungsbereich des jeweils anderen liegen. Er beginnt bei ±{initial} Elo und wächst alle {interval} Sekunden um {step} auf höchstens ±{maximum} Elo.',
  '{ruleset} queue · ±{window} Elo': '{ruleset}-Warteschlange · ±{window} Elo',
  '{value} waiting': '{value} Wartezeit',
  'Queue entries expire after {minutes} minutes or at the end of the season.':
    'Warteschlangeneinträge laufen nach {minutes} Minuten oder zum Saisonende ab.',
  'League standings': 'Ligatabelle',
  'Qualified players': 'Qualifizierte Spieler',
  'No players have qualified in this ruleset and season.':
    'Für dieses Regelwerk und diese Saison hat sich noch niemand qualifiziert.',
  'Provisional standings': 'Vorläufige Ligatabelle',
  'Finalized standings': 'Abgeschlossene Ligatabelle',
  'Standings being rebuilt': 'Ligatabelle wird neu berechnet',
  'Refresh standings': 'Ligatabelle aktualisieren',
  'Standings will return after the season rebuild completes.':
    'Die Tabelle ist nach der Neuberechnung der Saison wieder verfügbar.',
  'Revision {revision}': 'Revision {revision}',
  'Standings pages': 'Seiten der Ligatabelle',
  'Match history pages': 'Seiten des Ligaverlaufs',
  'Load older seasons': 'Ältere Saisons laden',
  'League match history': 'Ligaverlauf',
  'Refresh match history': 'Ligaverlauf aktualisieren',
  'You have no official league games in this ruleset and season.':
    'Du hast keine offiziellen Ligapartien in diesem Regelwerk und dieser Saison.',
  'Official league games': 'Offizielle Ligapartien',
  'Official result': 'Offizielles Ergebnis',
  'Awaiting official result': 'Offizielles Ergebnis ausstehend',
  Game: 'Partie',
  'Seasonal scoring': 'Saisonwertung',
  'Pending settlement': 'Wertung ausstehend',
  'Counts toward the season': 'Für die Saison gewertet',
  'Finished after the scoring deadline': 'Nach dem Wertungsschluss beendet',
  'Withdrawn from seasonal standings': 'Aus der Saisonwertung entfernt',
  'Corrected · revision {revision}': 'Korrigiert · Revision {revision}',
  'Open game': 'Partie öffnen',
  'View replay': 'Wiederholung ansehen',
  'League administration': 'Ligaverwaltung',
  'Pause matchmaking': 'Spielersuche pausieren',
  'Resume matchmaking': 'Spielersuche fortsetzen',
  'Cancel stale entry': 'Ungültigen Eintrag entfernen',
  'Rebuild season': 'Saison neu berechnen',
  'Matchmaking paused': 'Spielersuche pausiert',
  'Matchmaking resumed': 'Spielersuche fortgesetzt',
  'Stale entry cancelled': 'Ungültiger Eintrag entfernt',
  'Rebuild requested': 'Neuberechnung angefordert',
  'Season rebuilt': 'Saison neu berechnet',
  'League policy updated': 'Ligaeinstellungen aktualisiert',
  Reason: 'Grund',
  Action: 'Aktion',
  Season: 'Saison',
  Paused: 'Pausiert',
  Enabled: 'Aktiv',
  Expired: 'Abgelaufen',
  Expiry: 'Ablauf',
  'Account disabled': 'Konto deaktiviert',
  'Email unverified': 'E-Mail nicht bestätigt',
  'Account ineligible': 'Konto nicht zugelassen',
  'Stale queue pages': 'Seiten mit ungültigen Warteschlangeneinträgen',
  Worker: 'Worker',
  'Worker activity': 'Worker-Aktivität',
  'League audit entries': 'Liga-Protokolleinträge',
  'No league administration actions have been recorded.':
    'Es wurden keine Maßnahmen der Ligaverwaltung aufgezeichnet.',
  'Audit pages': 'Protokollseiten',
  'Queue entries': 'Warteschlangeneinträge',
  'Expired entries': 'Abgelaufene Einträge',
  'Pending settlements': 'Ausstehende Wertungen',
  'Failed settlements': 'Fehlgeschlagene Wertungen',
  'Settlements awaiting retry': 'Wertungen mit ausstehendem Wiederholungsversuch',
  'Oldest pending settlement': 'Älteste ausstehende Wertung',
  '{brandubh} Brandubh · {tablut} Tablut · {copenhagen} Copenhagen in queue':
    '{brandubh} Brandubh · {tablut} Tablut · {copenhagen} Kopenhagen in der Warteschlange',
  'Worker last checked {time}': 'Letzte Prüfung durch den Worker {time}',
  'Worker last succeeded {time}': 'Letzte erfolgreiche Verarbeitung durch den Worker {time}',
  'The last worker check failed. Results will be retried.':
    'Die letzte Prüfung des Workers ist fehlgeschlagen. Die Ergebnisse werden erneut verarbeitet.',
  'The league worker is waiting for the deployment lock.':
    'Der Liga-Worker wartet auf die Freigabe der Bereitstellungssperre.',
  Starting: 'Startet',
  Idle: 'Bereit',
  'Processing succeeded': 'Verarbeitung erfolgreich',
  'Waiting for deployment': 'Wartet auf Bereitstellung',
  'Settlement retry': 'Wertung wird erneut versucht',
  'Rebuild retry': 'Neuberechnung wird erneut versucht',
  'Processing deferred': 'Verarbeitung verschoben',
  'The last worker check encountered a processing failure.':
    'Bei der letzten Prüfung ist ein Verarbeitungsfehler aufgetreten.',
  '{count} consecutive worker failures': '{count} Verarbeitungsfehler in Folge',
  'Next settlement retry {time} · up to {count} attempts':
    'Nächster Wertungsversuch {time} · bis zu {count} Versuche',
  'Rebuilds awaiting retry · {count}': 'Ausstehende Neuberechnungsversuche · {count}',
  'Clearing contributions': 'Wertungsbeiträge werden geleert',
  'Clearing standings': 'Ligatabelle wird geleert',
  'Reading official games': 'Offizielle Partien werden eingelesen',
  'Recalculating standings': 'Ligatabelle wird neu berechnet',
  'Applying intervening results': 'Neue Ergebnisse werden nachgetragen',
  '{count} retry attempts · next retry {time}':
    '{count} Wiederholungsversuche · nächster Versuch {time}',
  'Rebuild in progress for {season}': 'Neuberechnung für {season} läuft',
  'Refresh league health': 'Ligastatus aktualisieren',
  'Stale league queue entries': 'Ungültige Liga-Warteschlangeneinträge',
  'Private audit history': 'Privates Verwaltungsprotokoll',
  'Rebuild {season} from official results? Standings are unavailable until recovery finishes.':
    '{season} anhand der offiziellen Ergebnisse neu berechnen? Die Tabelle bleibt bis zum Abschluss der Neuberechnung ausgeblendet.',
  'Enter a reason for this change.': 'Gib einen Grund für diese Änderung an.',
  'Season rebuild requested.': 'Neuberechnung der Saison angefordert.',
  'League administration updated.': 'Ligaverwaltung aktualisiert.',
  'Your account cannot access leagues.': 'Dein Konto hat keinen Zugriff auf Ligen.',
  'The league request is invalid.': 'Die Ligaanfrage ist ungültig.',
  'The league is temporarily read-only while deployment is in progress.':
    'Während der Bereitstellung sind Ligadaten vorübergehend schreibgeschützt.',
  'Sign in to continue.': 'Melde dich an, um fortzufahren.',
  'Your league state changed. Check the current status and try again.':
    'Dein Ligastatus hat sich geändert. Prüfe den aktuellen Status und versuche es erneut.',
  'Your league session changed. Refresh to continue.':
    'Deine Ligasitzung hat sich geändert. Aktualisiere die Daten, um fortzufahren.',
  'League request limit reached. Try again later.':
    'Das Limit für Ligaanfragen ist erreicht. Versuche es später erneut.',
  'Matchmaking requires an active, verified account with permission to play.':
    'Für die Spielersuche brauchst du ein aktives, verifiziertes Konto mit Spielberechtigung.',
  'This league queue request was not found.':
    'Diese Liga-Warteschlangenanfrage wurde nicht gefunden.',
  'The league service is temporarily unavailable.':
    'Der Ligadienst ist vorübergehend nicht verfügbar.',
  'These league records changed. Return to the first page to continue.':
    'Diese Ligaeinträge haben sich geändert. Kehre zur ersten Seite zurück.',
  'This league season was not found.': 'Diese Ligasaison wurde nicht gefunden.',
  'This league page is no longer available. Return to the first page.':
    'Diese Ligaseite ist nicht mehr verfügbar. Kehre zur ersten Seite zurück.',
  'This league record changed. Review the updated state before retrying.':
    'Dieser Ligaeintrag hat sich geändert. Prüfe den aktuellen Stand vor einem erneuten Versuch.',
  'A rebuild is already running for this season.':
    'Für diese Saison läuft bereits eine Neuberechnung.',
  'This queue entry is no longer stale.': 'Dieser Warteschlangeneintrag ist wieder gültig.',
  'This league action is unavailable to your account.':
    'Diese Ligaaktion ist für dein Konto nicht verfügbar.',
  'The league request could not be completed. Try again.':
    'Die Ligaanfrage konnte nicht abgeschlossen werden. Versuche es erneut.',
};
export default leagues;
