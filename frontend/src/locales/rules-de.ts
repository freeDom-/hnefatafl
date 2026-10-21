import { getLocale } from '../i18n';

const rules: Record<string, string> = {
  tablut: `# Tablut — Marktregeln nach Aage Nielsen

Diese Variante folgt dem verlinkten Markt-Faltblatt mit sechs Regeln. Sie unterscheidet sich von historischen samischen Rekonstruktionen und Turniervarianten.

## Brett und Aufstellung

Auf dem 9 × 9-Brett stehen 16 Angreifer gegen acht Verteidiger und ihren König. **Die Verteidiger beginnen.** Danach wird abwechselnd gezogen. Der König gehört zu den Verteidigern.

## Bewegung

Eine eigene Figur zieht beliebig weit waagerecht oder senkrecht über freie Felder. Diagonale Züge, Überspringen und das Ziehen auf besetzte Felder sind verboten. Diese Bewegung gilt auch für den König.

Alle Felder sind gewöhnliche Felder. Auch einfache Figuren dürfen auf der Mitte und den Ecken stehen oder darüber ziehen. Mitte, Ecken und Brettrand helfen nicht beim Fangen.

## Gefangennahme

Eine gegnerische Figur wird gefangen, wenn der eigene Zug sie zwischen zwei eigenen Figuren waagerecht oder senkrecht einschließt. Mehrere solche Klammern dürfen mit demselben Zug geschlossen werden. Die gefangenen Figuren werden sofort entfernt.

Der König darf beim Fangen helfen und wird selbst durch eine gewöhnliche Klammer aus **zwei Angreifern** gefangen — auch auf oder neben der Mitte. Eine Figur darf gefahrlos zwischen zwei Gegner ziehen: Nur ein schließender Zug des Gegners fängt sie.

## Sieg und digitale Zusatzkonventionen

- Die Verteidiger gewinnen, sobald der König **ein beliebiges Randfeld** erreicht.
- Die Angreifer gewinnen, sobald sie den König fangen.
- Die Anwendung erklärt die dritte Wiederholung derselben Stellung mit derselben Seite am Zug zum Unentschieden.
- Hat die nächste Seite keinen legalen Zug, endet die Partie ebenfalls unentschieden.

Wiederholung und Zugunfähigkeit sind ausdrücklich digitale Konventionen; das Faltblatt legt sie nicht fest. Die Spielregeln enthalten kein Zuglimit. Trainings- und Turnierlimits werden separat als nicht beendete Partien ausgewiesen.

## Quelle

[Aage Nielsens englisches Markt-Faltblatt](https://aagenielsen.dk/tablutmarketregler_engelsk.pdf), einschließlich Aufstellungsdiagramm; gelesen am 21. Oktober 2026. Engine-Kennung: \`tablut\`, Regelversion 1.`,
  brandubh: `# Brandubh — Faltblatt von Cyningstan

Brandubh ist ein kleines irisches Tafl-Spiel. Diese Variante folgt Damian Walkers Rekonstruktion im Faltblatt „Traditional Board Game Series“, Nr. 24, zweite Auflage 2011.

## Brett und Aufstellung

Auf dem 7 × 7-Brett stehen acht Angreifer gegen vier Verteidiger und ihren König. **Die Angreifer beginnen.** Der König gehört zu den Verteidigern.

## Bewegung und besondere Felder

Eine eigene Figur zieht beliebig weit waagerecht oder senkrecht über freie Felder. Diagonale Züge, Überspringen und besetzte Zielfelder sind verboten.

Nur der König darf auf der **Mitte oder einer Ecke stehen bleiben**. Andere Figuren dürfen die leere Mitte überqueren. Der König darf zur Mitte zurückkehren.

## Gefangennahme

Eine gegnerische Figur wird durch eine waagerechte oder senkrechte Klammer aus zwei eigenen Figuren gefangen. Der König darf beim Fangen helfen und wird selbst durch eine gewöhnliche Zweierklammer gefangen, auch auf der Mitte. Ein Zug kann mehrere Figuren fangen.

Eine leere Ecke darf eine der beiden fangenden Figuren ersetzen. **Die Mitte hat diese Wirkung nicht.** Der Brettrand ist ebenfalls nicht feindlich. Das freiwillige Ziehen zwischen zwei Gegner ist sicher; zum Fangen muss der Gegner die Klammer selbst schließen.

Es gibt keine Schildwall-Gefangennahme, Einkesselungs- oder Fluchtburg-Siege.

## Sieg und Unentschieden

- Die Verteidiger gewinnen, wenn der König eine der **vier Ecken** erreicht. Ein gewöhnliches Randfeld genügt nicht.
- Die Angreifer gewinnen durch Gefangennahme des Königs.
- Hat die nächste Seite keinen legalen Zug, endet die Partie unentschieden — auch wenn der letzte Angreifer gefangen wurde.
- Die dritte Wiederholung derselben Stellung mit derselben Seite am Zug führt zum Unentschieden. Das Faltblatt erlaubt Remis durch Wiederholung; der genaue Schwellenwert ist eine digitale Konvention.

Das Regelwerk enthält kein Zuglimit. Trainings- und Turnierlimits werden getrennt ausgewiesen.

## Quelle

[Cyningstans Brandub-Faltblatt](http://www.cyningstan.com/data-download/395/brandub-leaflet), 13 nummerierte Regeln und Aufstellungsdiagramm; gelesen am 21. Oktober 2026. Die Regeln sind hier sinngemäß wiedergegeben; das Original stammt von Damian Walker. Engine-Kennung: \`brandubh\`, Regelversion 1.`,
  copenhagen: `# Kopenhagen-Hnefatafl

Diese moderne Variante wurde 2012 von Aage Nielsen, Adam Bartley und Tim Millar formuliert. Sie ergänzt Schildwall-Gefangennahme, Einkesselung und Fluchtburgen.

## Brett und Bewegung

Auf dem 11 × 11-Brett stehen 24 Angreifer gegen zwölf Verteidiger und ihren König. **Die Angreifer beginnen.**

Eine eigene Figur zieht beliebig weit waagerecht oder senkrecht über freie Felder, ohne zu springen. Der König zieht genauso. Nur er darf auf dem Thron in der Mitte oder auf einer Ecke stehen bleiben. Alle Figuren dürfen den leeren Thron überqueren; der König darf dorthin zurückkehren.

## Feindliche Felder

Die Ecken unterstützen Gefangennahmen. Der Thron ist für Angreifer immer feindlich, für Verteidiger nur, wenn er leer ist. Ein Verteidiger neben dem besetzten Thron kann also nicht gegen ihn gefangen werden. Der König darf dagegen einen Angreifer gegen seinen besetzten Thron fangen. Der Brettrand ist nicht feindlich.

## Gewöhnliche Figuren und König

Eine gegnerische Figur wird gefangen, wenn ein Zug eine waagerechte oder senkrechte Klammer aus eigenen Figuren oder einer eigenen Figur und einem feindlichen Feld schließt. Der König darf beim Fangen helfen. Freiwillig zwischen Gegner zu ziehen ist sicher.

Der König wird nur gefangen, wenn Angreifer **alle vier Nachbarfelder** besetzen. Neben dem leeren Thron genügen drei Angreifer und der Thron. Der schließende Zug muss vom Angreifer ausgehen. **Am Brettrand kann der König nicht gefangen werden.**

## Schildwall

Mindestens zwei zusammenhängende gegnerische Figuren am Brettrand werden gemeinsam gefangen, wenn:

1. Beide Enden der Reihe von eigenen Figuren begrenzt werden; eine leere Ecke darf eine Endfigur ersetzen.
2. Hinter jeder gegnerischen Figur unmittelbar nach innen eine eigene Figur steht.
3. Der aktuelle Zug eine Endfigur oder eine innere Stützfigur ergänzt und so den Wall schließt.

Ein einzelner Randstein oder eine fehlende Stütze genügt nicht. Der König kann Endfigur oder Stütze sein. Er zählt auch innerhalb einer bedrohten Verteidigerreihe, **überlebt selbst aber die Schildwall-Gefangennahme**. Gewöhnliche Figuren der Reihe werden entfernt.

## Siege

- Die Verteidiger gewinnen durch Erreichen einer Ecke.
- Die Angreifer gewinnen durch Gefangennahme des Königs.
- Die Angreifer gewinnen auch, wenn **alle verbleibenden Verteidiger einschließlich des Königs** durch eine ununterbrochene Angreiferbarriere von jedem Brettrand abgeschnitten sind. Ein Verteidiger außerhalb des Rings verhindert diesen Sieg.
- Die Verteidiger gewinnen durch eine **Fluchtburg**: Der König berührt den Rand, hat Bewegungsraum und eine unzerstörbare Verteidigermauer verhindert dauerhaft den Zugang der Angreifer.

Die Engine prüft Fluchtburgen strukturell: Sie erweitert den von Angreifern erreichbaren Außenraum und entfernt wiederholt potenziell fangbare Mauersteine, einschließlich Schildwallbedingungen. Nur wenn selbst dieser maximale Angriff den beweglichen Raum des Königs nicht erreicht, ist die Burg fertig. Eine Lücke, fangbare Mauer oder ein unbeweglicher König genügt nicht. Eine erst durch weitere Verteidigerzüge entstehende Burg gilt noch nicht als abgeschlossen.

## Wiederholung und Zugunfähigkeit

Wiederholung verliert für die Verteidiger. Die Anwendung verwendet die **dritte Wiederholung derselben Stellung mit derselben Seite am Zug** als dokumentierten digitalen Schwellenwert. Die erste Wiederholung wird nicht verboten.

Eine Seite ohne legalen Zug verliert. Es gibt keine automatische Niederlage nach 140 Zügen und keinen zusätzlichen Eckblockade-Sieg. Trainings- und Turnierlimits sind eigene, ausdrücklich ausgewiesene Budgets.

## Quellen und Auslegung

Die [Primärquelle von Aage Nielsen](https://aagenielsen.dk/copenhagen_rules.php) lieferte bei der Recherche am 21. Oktober 2026 HTTP 455. Die Regeln wurden mit der [illustrierten Abschrift](https://hnefatafl.org/rules.html) und [Cyningstans Beschreibung](http://tafl.cyningstan.com/page/768/copenhagen-hnefatafl-rules) abgeglichen.

Zusatzlimits der illustrierten Abschrift werden nicht übernommen. Diese Umsetzung erlaubt wie deren moderner Kern auch das Schließen eines Schildwalls durch die letzte innere Stütze; die ältere Cyningstan-Auslegung erlaubt nur eine Endfigur. Die direkte Prüfung der nicht erreichbaren Primärquelle bleibt offen. Engine-Kennung: \`copenhagen\`, Regelversion 1.`,
};

export function localizedRules(ruleset: string, original: string) {
  return getLocale() === 'de-DE' ? (rules[ruleset] ?? original) : original;
}
