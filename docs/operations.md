# Agent Workbench bedienen

Die Befehle laufen im Repository auf dem Docker-Host. Benötigt werden Docker
Engine mit Compose v2, GNU Make und Python 3. Der Desktop benötigt keinen
Docker-Socket. `make help` zeigt die verfügbaren Ziele.

## Im Alltag

| Befehl | Wirkung |
| --- | --- |
| `make up` | Lokale Konfiguration vorbereiten, Image bauen und Desktop starten |
| `make up MEMORY=1` | Zusätzlich den optionalen Hindsight-Dienst starten |
| `make status` | Zustand der Dienste anzeigen |
| `make logs` | Die letzten Dienstmeldungen anzeigen und weitere verfolgen |
| `make doctor` | Dienststatus und den vorhandenen Desktop-Healthcheck prüfen |
| `make codex` / `make claude` | Eine interaktive Agent-Session öffnen |
| `make shell` | Eine Shell im laufenden Desktop öffnen |
| `make down` | Dienste und Compose-Netzwerk entfernen, Datenvolumes behalten |

`doctor` prüft noVNC, VNC und eine tatsächliche Desktop-Aufnahme. Er führt keine
Modellanfragen aus und bestätigt keine Konto- oder Providerberechtigungen.
Beim Beenden der Dienste enden auch interaktive CLI-Prozesse; später eine neue
Session starten. `make down` verwendet kein `--volumes`.

## Anmeldungen

`make github-login` meldet die GitHub CLI an; GitHub MCP übernimmt dieselbe
Anmeldung bei seinem nächsten Start. `make memory-login` richtet eine getrennte
Codex-Anmeldung für Hindsight ein. Die Konten von Codex und Claude werden in
den jeweiligen interaktiven CLIs eingerichtet. Zugangsdaten bleiben zur Laufzeit
im persistenten Home bzw. im separaten Hindsight-Auth-Volume.

## Bauen und prüfen

```sh
make test
make smoke
```

`test` baut `agent-workbench:dev`, führt Python- und Node-Policy-Tests in einem
wegwerfbaren Container ohne externen Netzwerkzugang aus und validiert Compose.
Der Checkout ist für die Tests nur lesbar eingebunden. Für diese Tests werden
keine Python- oder Node-Abhängigkeiten auf dem Host installiert.

`smoke` prüft das bereits gebaute Image in einem eigenen temporären Container:
Desktop, CLI-Versionen, echte Playwright-MCP-Navigation mit Formular und
Screenshot, den lokalen Jev-Bildadapter und CUA-Beobachtung samt Terminaleingabe.
Der Test verwendet lokale Seiten und keine Modellzugänge. Sein Container und
sein temporäres VNC-Passwort werden danach entfernt.

`make build` baut nur das Entwicklungsimage. Mit `DEV_IMAGE` lässt sich dessen
Tag ändern; dieselbe Einstellung für `test` und `smoke` verwenden:

```sh
make test DEV_IMAGE=agent-workbench:experiment
make smoke DEV_IMAGE=agent-workbench:experiment
```

Proxy- und CA-Einstellungen für besondere Build-Umgebungen sind in der
[Docker-Anleitung](docker.md) beschrieben. Die
[CI-Anleitung](ci.md) erklärt die Prüfungen, Dependabot und Veröffentlichungen.

## Sichern und wiederherstellen

Die Ziele `backup-init`, `backup`, `snapshots`, `backup-check` und `restore`
verwenden den Restic-Helfer. Speicherort und Schlüssel können über
`BACKUP_DIR` und `BACKUP_PASSWORD_FILE` gewählt werden. Der Schlüssel gehört
nicht in das Backup-Repository. Ein Restore erwartet eine leere Zielumgebung;
Details und Umzugsanleitung stehen unter [Backups](backups.md).

`make backup-test` prüft Verschlüsselung und Wiederherstellung in separaten
temporären Compose-Projekten mit Testdaten. Er verwendet keine vorhandenen
Benutzer-Volumes oder Anmeldungen.
