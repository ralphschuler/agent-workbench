# Verschlüsselte Backups und Restore

Die Sicherung verwendet [restic 0.19.1](https://github.com/restic/restic/releases/tag/v0.19.1),
kein eigenes Verschlüsselungsverfahren. Der offizielle Linux-amd64-Helper ist auf
`restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510`
festgelegt. Docker prüft beim Pull den Digest. Alle Helper laufen ohne Netzwerk
und ohne Docker-Socket; nur das Host-Skript steuert Docker. Voraussetzungen sind
Linux, Python 3.9+, Docker Engine und Docker Compose.

```bash
make backup-init
make backup
make snapshots
make backup-check
```

`backup-init` erzeugt das lokale Repository `.backups/restic` und einen zufälligen
Wiederherstellungsschlüssel in `.secrets/backup_password` mit Modus `0600`.
Vorhandene Schlüssel werden erhalten; ein vorhandenes oder nicht leeres
Repository wird niemals neu initialisiert. Ein eigenes Repository oder eine
bestehende Schlüsseldatei lassen sich mit `BACKUP_DIR` und
`BACKUP_PASSWORD_FILE` auswählen; relative Pfade beziehen sich auf den Checkout.
Diese Variablen werden als Shell-Umgebung übergeben, nicht aus `.env` geladen.
Die Schlüsseldatei muss privat und nicht leer sein.

Den Schlüssel **separat und sicher** aufbewahren, beispielsweise im
Passwortmanager. Er ist ausdrücklich vom gesicherten Konfigurationsverzeichnis
ausgeschlossen. Ohne ihn gibt es keine Wiederherstellung. Das lokale Repository
ist verschlüsselt, schützt aber allein nicht vor einem verlorenen Datenträger:
Repository und getrennt verwahrten Schlüssel zusätzlich auf ein anderes Medium
übertragen. Der Host-Benutzer besitzt die Repository-Dateien und kann sie kopieren.
Dateinamen, Zugangsdaten und Inhalte liegen im Repository verschlüsselt; dessen
Größe und Dateizeitpunkte bleiben sichtbar.

## Inhalt und Konsistenz

Eine Sicherung umfasst die tatsächlich vorhandenen Compose-Volumes
`agent-home`, `hindsight-data` und `hindsight-auth`, den gemounteten Workspace
sowie `.env` und `.secrets` ohne Wiederherstellungsschlüssel. Darin liegen auch
Browserprofile, CLI-Anmeldungen und Hindsight-Daten. Die konkreten Volume-Namen
werden aus der aufgelösten Compose-Konfiguration ermittelt. Fehlende optionale
Volumes werden vermerkt und nicht künstlich angelegt. Repository und Schlüssel
müssen außerhalb des Workspace liegen; das Repository auch außerhalb `.secrets`.

`backup` stoppt die aktuell laufenden Dienste `agent` und `hindsight` für eine
kalte Sicherung, wartet bis zu 60 Sekunden auf einen sauberen Stopp und startet
danach genau dieselben zuvor laufenden Container wieder. Ein erzwungener Stopp
oder fremde laufende Container auf diesen Volumes verhindern die Sicherung.
Neustart wird auch bei einem Fehler, Strg+C oder SIGTERM versucht; SIGKILL,
Host-Absturz oder ein ausgefallener Docker-Daemon können das verhindern.

Backups bei abgeschlossenen/ruhenden Aufgaben ausführen: Der Stopp beendet auch
interaktive Codex-/Claude-Prozesse und `docker exec`-Terminals. Der Container-Neustart
setzt solche Prozesse nicht fort; anschließend neu verbinden. Während der
Sicherung auch auf dem Host keine Workspace- oder Konfigurationsdateien ändern
und keine weiteren Container starten. Das Skript sperrt parallele eigene
Backup-Operationen; externe Programme müssen ebenfalls ruhen.

`backup-check` führt `restic check --read-data` aus und liest dabei die
verschlüsselten Daten vollständig. Backups mit restic-Fehlern oder unvollständig
gelesenen Dateien gelten als fehlgeschlagen, selbst wenn restic eine Snapshot-ID
angelegt hat. Es gibt kein automatisches Löschen alter Snapshots.
Nur vollständig erfolgreiche Sicherungen erhalten den freigegebenen Snapshot-Tag;
unvollständige oder abgebrochene Snapshots bleiben als `pending` markiert und
werden weder angezeigt noch durch `latest` ausgewählt.

## Auf einem frischen Checkout wiederherstellen

Zuerst einen frischen Checkout der passenden Version anlegen; **vorher weder
`make init` noch `make up` ausführen**. `.env` und `.secrets` müssen fehlen,
Workspace und Ziel-Volumes müssen leer sein, Ziel-Dienste müssen gestoppt sein.
Repository und Schlüssel vom Sicherungsmedium bereitstellen; den Schlüssel
außerhalb des Ziel-Checkouts speichern und mit `chmod 600` schützen.

```bash
export BACKUP_DIR=/mnt/backup/agent-workbench/restic
export BACKUP_PASSWORD_FILE=/secure/agent-workbench-recovery-key
# Optional: anderer Compose-Projektname, z.B. auf demselben Docker-Host.
export COMPOSE_PROJECT_NAME=agent-workbench-restored
make snapshots
make restore SNAPSHOT=FULL_SNAPSHOT_ID
# Ohne SNAPSHOT wird der neueste Agent-Workbench-Snapshot ausgewählt.
```

Der Restore prüft zuerst den vollständigen Snapshot in einem privaten temporären
Verzeichnis mit `restic restore --verify`. Erst danach stellt er in die leeren
Ziele wieder her. Dafür muss zusätzlich zum Ziel genug temporärer Speicher für
die entpackte Sicherung frei sein (`TMPDIR` kann einen anderen Datenträger wählen).
Dateiinhalte und die vom restic-Dateisystem unterstützten Metadaten werden
wiederhergestellt. Volume- und Workspace-UIDs bleiben erhalten, insbesondere
UID/GID 1000 des Desktop-Benutzers. `.env` und `.secrets` gehören dagegen dem
aktuellen Host-Benutzer, damit Compose auch auf einem Host mit anderer UID die
privaten Dateien lesen kann. Ein anderer Host-UID ändert nicht den bestehenden
Linux-UID-1000-Vertrag für den Workspace; keine pauschalen Schreibrechte vergeben.

Vorhandene Daten werden verweigert; es gibt kein `--force`. Ein Fehler während
der letzten Kopierschritte kann bereits befüllte, ursprünglich leere Ziele
zurücklassen. Dienste bleiben gestoppt. Diese Ziele prüfen und gegebenenfalls
eine neue leere Projektumgebung wählen; das Skript löscht solche Daten nicht.

Nach dem Restore `.env` auf Host-Ports/Projekt-Einstellungen prüfen und den
Wiederherstellungsschlüssel weiterhin extern behalten. Bei Migration zuerst den
alten Stack stoppen, dann `make up` beziehungsweise `make up MEMORY=1` im Ziel
ausführen. So nutzen nicht zwei Hindsight-Instanzen dieselben kopierten
OAuth-Tokens gleichzeitig. Abgelaufene oder widerrufene Anmeldungen können eine
erneute Anmeldung erfordern. Ein authentifizierter Restore belegt noch keinen
erfolgreichen PostgreSQL-/Desktop-Start; danach `make doctor` ausführen.
Hindsight wird als kalte Dateisicherung seines eingebetteten PostgreSQL gesichert.
Mit dem passenden/kompatiblen gepinnten Hindsight-Image wiederherstellen; ein
Datei-Roundtrip belegt keinen Datenbank-Upgrade-Pfad.

## Prüfung

`make backup-test` (oder `bash scripts/test-backup.sh`) prüft einen echten
isolierten restic-Durchlauf mit zufällig benannten Compose-Projekten und eigenen
Volumes: Snapshot, Neustart derselben Container, Restore in einen anderen
Projektnamen, private Konfiguration, Browser-/OAuth-/Speicherdateien, UID 1000,
falscher Schlüssel, manipuliertes Repository und Überschreibungsverweigerung.
Die Fixture-Dienste enthalten Testdateien, keinen echten Browser oder
PostgreSQL-Prozess. Der Test greift nicht auf lokale Benutzer-Volumes oder
Zugangsdaten zu. Zusätzlich prüfen Unit-Tests Fehler-/Abbruchbehandlung und
Schlüsselausschluss. Modellaufrufe und die Gültigkeit gesicherter Cloud-Logins
sind davon unabhängig.

Offizielle Referenzen: [Container](https://restic.readthedocs.io/en/stable/020_installation.html#docker-container),
[Verschlüsselung](https://restic.readthedocs.io/en/stable/100_references.html),
[Restore](https://restic.readthedocs.io/en/stable/050_restore.html),
[Scripting und Exitcodes](https://restic.readthedocs.io/en/stable/075_scripting.html).
