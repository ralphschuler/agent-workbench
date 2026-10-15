# Agent Workbench

[![CI](https://github.com/ralphschuler/agent-workbench/actions/workflows/ci.yml/badge.svg)](https://github.com/ralphschuler/agent-workbench/actions/workflows/ci.yml)
![Ubuntu 24.04](https://img.shields.io/badge/Ubuntu-24.04-E95420?logo=ubuntu&logoColor=white)
![Linux amd64](https://img.shields.io/badge/Plattform-linux%2Famd64-2496ED?logo=docker&logoColor=white)

Ein persistenter Ubuntu-Desktop für **Codex und Claude Code** – mit Chrome,
Terminal, Computer Use und gemeinsamen Skills. CLI und Desktop-Werkzeuge
arbeiten in derselben Sitzung; Jev und Hindsight ergänzen Entscheidungen und
Gedächtnis.

## Was enthalten ist

| Bereich | Ausstattung |
| --- | --- |
| Desktop | Ubuntu 24.04, XFCE, Chrome, Terminal und noVNC |
| Coding | Codex und Claude Code |
| GitHub | `gh`-CLI und offizieller GitHub-MCP-Server, einschließlich Actions-Werkzeugen |
| Computer Use | Cua Driver mit Fensterbeobachtung, Screenshots und Eingaben |
| Browser | Offizieller Playwright-MCP-Server im vorhandenen Chrome |
| Entscheidungen | Jev-MCP für gebündelte, typisierte Fragen mit Unsicherheitsbewertung |
| Gedächtnis | Optionales Hindsight mit MCP und Recall-/Retain-Hooks |
| Skills | Desktop, Jev, Memory sowie Anthropic Frontend Design und Skill Creator |
| Automatisierung | Dependabot, geprüfte Patch-/Minor-Merges, CI, GHCR und GitHub Releases |
| Betrieb | Einheitliche Make-Befehle und verschlüsselte Restic-Backups |

## Schnellstart

Benötigt werden Docker Engine mit Compose v2, Make, Python 3, Linux **amd64**
und mindestens 4 GB RAM. Die Make-Befehle laufen auf dem Docker-Host.

```sh
git clone https://github.com/ralphschuler/agent-workbench.git
cd agent-workbench
make up
```

Den Desktop unter **<http://localhost:6080>** öffnen. Das lokal erzeugte
VNC-Passwort steht in `.secrets/vnc_password`; `init.sh` erhält vorhandene
Konfiguration. Für entfernte Hosts einen SSH-Tunnel verwenden.

Die Coding-Assistenten im Container starten und mit dem eigenen Konto anmelden:

```sh
make codex
# alternativ:
make claude
```

Chrome läuft mit aktivierter Sandbox, der Container als Benutzer `agent`
(UID 1000). Host-Rechte und Workspace-Eigentümer erklärt die
[Desktop-Anleitung](docs/docker.md).

`make help` zeigt alle Befehle, darunter `make logs`, `make doctor`,
`make test` und `make down`. [Bedienung und Tests →](docs/operations.md)

## Anweisungen für Agent-Sessions

Eigene globale Anweisungen stehen im Container in **`/home/agent/AGENTS.md`**.
Codex lädt sie über `~/.codex/AGENTS.md`, Claude über `~/.claude/CLAUDE.md`.
Änderungen bleiben im Home-Volume erhalten und gelten für neue Sessions.
Vorhandene eigene CLI-Anweisungsdateien werden nicht überschrieben.

Die [Vorlage](docker/home-AGENTS.md) wird nur bei der ersten Einrichtung
übernommen. Die `AGENTS.md` im Repository beschreibt separat die Regeln für
Agenten, die an diesem Repository arbeiten.

## Integrationen aktivieren

### GitHub

```sh
make github-login
```

Der GitHub-MCP-Server übernimmt die `gh`-Anmeldung bei seinem nächsten Start.
Alternativ lassen sich Tokens zur Laufzeit über `.env` bereitstellen. In der
MCP-Konfiguration werden keine Tokenwerte gespeichert.
[Anmeldung und Werkzeuge →](docs/github.md)

### Jev

`TYPESAFE_API_KEY` in `.env` setzen und den Agent-Container neu erstellen:

```sh
make up
```

Jev bewertet konkrete Alternativen und mehrere Ja/Nein-Fragen in einer Anfrage.
Unsichere Ergebnisse führen zur Enthaltung. Screenshots werden nur bei einem
ausdrücklichen Werkzeugaufruf übertragen.
[Jev und CUA →](docs/jev.md)

### Playwright MCP

Playwright ist für Codex und Claude vorkonfiguriert und bedient Webseiten über
DOM und Accessibility. Es verwendet Chrome mit einem eigenen persistenten
Profil. Jev bewertet bei Bedarf mehrere begrenzte Fragen aus derselben frischen
Beobachtung; anschließend führt Playwright den gewählten Schritt aus und prüft
das Ergebnis. CUA bleibt für Desktop und Browser-Oberflächen außerhalb der Seite.
[Browser und Jev →](docs/browser.md)

### Hindsight

Hindsight verwendet standardmäßig eine **separate Codex-/ChatGPT-Anmeldung**.
Ein zusätzlicher OpenAI-API-Schlüssel ist dafür nicht nötig.

```sh
make memory-login
```

Danach in `.env` `HINDSIGHT_ENABLED=1` setzen. Automatische Recall-/Retain-Hooks
lassen sich zusätzlich mit `HINDSIGHT_HOOKS_ENABLED=1` aktivieren.

```sh
make up MEMORY=1
```

`init.sh` erzeugt den separaten Schlüssel für die lokale Hindsight-API.
Hindsight erhält keinen öffentlichen Host-Port.
[Anmeldung, Speicher und Hooks →](docs/hindsight.md)

## Backups

```sh
make backup-init
make backup
make snapshots
```

Restic verschlüsselt Home, Browserprofile, Hindsight, Workspace und lokale
Konfiguration. Den Wiederherstellungsschlüssel separat aufbewahren. Die
Sicherung stoppt laufende Dienste kurz; Agent-Aufgaben vorher beenden.
Restore ist für eine leere Zielumgebung vorgesehen und überschreibt keine
vorhandenen Daten. [Sicherung und Wiederherstellung →](docs/backups.md)

## Daten und Konfiguration

| Ort | Inhalt |
| --- | --- |
| `workspace/` | Arbeitsdateien; im Container unter `/workspace` |
| Volume `agent-home` | CLI-Anmeldungen, Benutzerkonfiguration und Browserprofil |
| Volume `hindsight-data` | Persistente Erinnerungen |
| Volume `hindsight-auth` | Eigene Codex-Anmeldung für Hindsight |
| `.env` und `.secrets/` | Lokale Zugangsdaten; von Git und Image-Build ausgeschlossen |
| `.backups/restic/` | Verschlüsseltes Backup-Repository; von Git und Image-Build ausgeschlossen |

`docker compose down` erhält die benannten Volumes; `docker compose down -v`
löscht sie. Vorhandene Einstellungen und eigene Skills bleiben beim Ergänzen
der verwalteten Agent-Konfiguration erhalten.

## Builds und Updates

Pull Requests durchlaufen Tests sowie einen vollständigen Image-Build mit
Desktop-, Chrome- und CUA-Smoke-Test. Erfolgreiche Builds auf `main`
veröffentlichen **`ghcr.io/ralphschuler/agent-workbench:latest`**; Versions-Tags
wie `v1.2.3` erzeugen zusätzlich versionierte Images und GitHub Releases.

Dependabot aktualisiert Actions, Docker-, npm- und Python-Abhängigkeiten.
Verifizierte Patch-/Minor-Updates können nach erfolgreicher CI automatisch
gemergt werden. Majors bleiben zur manuellen Prüfung offen.

## Dokumentation

| Thema | Anleitung |
| --- | --- |
| Befehle für Alltag und Entwicklung | [Bedienung](docs/operations.md) |
| Desktop, Sandbox und Host-Voraussetzungen | [Docker](docs/docker.md) |
| GitHub-Anmeldung und MCP | [GitHub](docs/github.md) |
| Gedächtnis und automatische Hooks | [Hindsight](docs/hindsight.md) |
| Entscheidungen und Computer Use | [Jev und CUA](docs/jev.md) |
| Browser-Automatisierung | [Playwright MCP](docs/browser.md) |
| Verschlüsselte Sicherung und Restore | [Backups](docs/backups.md) |
| Gemeinsame Skills und Evaluationen | [Skills](docs/skills.md) |
| Tests, Releases und Repository-Einstellungen | [CI und Updates](docs/ci.md) |
