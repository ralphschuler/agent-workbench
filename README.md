# Agent Desktop

Ubuntu-24.04-Desktop für Coding- und Computer-Use-Agenten: Chrome, XFCE und
Terminal, Codex, Claude Code und Gemini CLI, Cua Driver, Jev und optional
Hindsight. Das Image läuft als Benutzer `agent` (UID 1000). Der Desktop und alle
Agenten teilen dieselbe X11- und D-Bus-Sitzung.

## Starten

Voraussetzungen: Docker Engine mit Compose v2, Linux **amd64**, mindestens
4 GB RAM und ausreichend Platz für das Desktop-Image. Dieses Image wird
zunächst nur für `linux/amd64` gebaut und getestet.

```sh
bash scripts/init.sh
docker compose up --build -d
docker compose ps
docker compose exec agent codex
# Alternativen:
docker compose exec agent claude
docker compose exec agent gemini
```

`scripts/init.sh` erzeugt lokale Konfiguration und ein zufälliges achtstelliges
VNC-Passwort in `.secrets/vnc_password` und einen lokalen Hindsight-API-Schlüssel
in `.env`, ohne bestehende Dateien zu ersetzen.
Der noVNC-Desktop ist über Port `6080` auf der Loopback-Adresse des Docker-Hosts
erreichbar. Das Passwort liegt in der genannten Datei. Bei einem entfernten Host
einen SSH-Tunnel verwenden; VNC/noVNC nicht unverschlüsselt ins Internet stellen.

`./workspace` wird nach `/workspace` gemountet. Das Volume `agent-home` hält
CLI-Anmeldungen, Konfiguration und Browserprofil. `docker compose down` erhält
die Daten; `docker compose down -v` löscht die benannten Volumes.

Die CLIs sind installiert, ihre Konten jedoch nicht vorkonfiguriert. API-Zugänge
in der ignorierten `.env` setzen oder sich interaktiv in der jeweiligen CLI
anmelden. Nach Änderungen an `.env` den Container neu erstellen:

```sh
docker compose up -d --force-recreate agent
```

Der Chrome-Sandbox bleibt standardmäßig eingeschaltet. Je nach Host sind
zusätzliche Namespace-Rechte nötig; siehe [Desktop und Sandbox](docs/docker.md).
Das Image erhält weder privilegierten Modus noch Zugriff auf den Docker-Socket.

## Integrationen

| Bestandteil | Umsetzung |
| --- | --- |
| Coding-CLIs | Codex, Claude Code und Gemini CLI; npm-Versionen und Integrität im Lockfile |
| Computer Use | `cua-driver mcp` über stdio; gemeinsame Desktop-Sitzung und Accessibility |
| Jev | `jev_decide` und `jev_assess` als MCP-Werkzeuge mit offizieller Typesafe-SDK |
| Hindsight | Optionaler Compose-Dienst, HTTP-MCP und opt-in Recall-/Retain-Hooks |
| Common Skills | Desktop, Jev-Entscheidungen, Memory sowie Anthropic Frontend Design und Skill Creator für die drei CLIs |
| Updates und Releases | Dependabot-Prüfungen, kontrollierte Patch-/Minor-Merges, GHCR-Pipeline |

Jev wird mit `TYPESAFE_API_KEY` aktiviert. Es bewertet konkrete Alternativen und
gebündelte Ja/Nein-Fragen; es ersetzt keine Recherche oder Freigabe. Unsichere
Ergebnisse führen zu einer Enthaltung. Screenshots werden nur auf ausdrücklichen
Werkzeugaufruf hochgeladen. Details, Beispiele und der Plan zur Messung von
Geschwindigkeit und Entscheidungsqualität stehen in [Jev und CUA](docs/jev.md).

Für Hindsight die `.env` um den passenden Modellanbieter-Zugang ergänzen und
den von `init.sh` erzeugten `HINDSIGHT_API_KEY` für die lokale API verwenden.
Bei einer manuell angelegten `.env` einen eigenen zufälligen API-Schlüssel setzen.
`HINDSIGHT_ENABLED=1`
registriert MCP; `HINDSIGHT_HOOKS_ENABLED=1` aktiviert zusätzlich automatische
Recall-/Retain-Hooks für Codex und Claude. Dann:

```sh
docker compose --profile memory up --build -d
docker compose --profile memory ps
```

Hindsight-API und UI erhalten keinen Host-Port. Die getrennten Zugangsdaten,
Persistenz und Datenschutzgrenzen sind in [Hindsight](docs/hindsight.md)
beschrieben. Für Gemini werden vorhandene `TYPESAFE_API_KEY` und gegebenenfalls
`HINDSIGHT_API_KEY` explizit in der Umgebungsfilterung freigegeben; diese Freigabe
gilt dort für alle MCP-Server. Eigene Blockierregeln bleiben erhalten.

Der Konfigurationsgenerator ergänzt verwaltete MCP-Einträge, Hook-Kommandos und
Skill-Verknüpfungen. Vorhandene Benutzereinstellungen und Änderungen bleiben
erhalten; Konflikte werden gemeldet. Eigene Skills als zusätzliche Verzeichnisse
mit `SKILL.md` unter `skills/` ablegen und das Image neu bauen.

Die offiziellen Anthropic-Skills `frontend-design` und `skill-creator` sind
unverändert mit ihren Lizenzen und einer fixierten Herkunftsversion enthalten.
Mit dem Skill-Creator wurden die lokalen Skills `desktop` (CUA) und `decisions`
(Jev) überarbeitet und gegen ihre Vorgänger geprüft. Szenarien, Herkunft und
Grenzen dieses Vergleichs stehen unter [Skills](docs/skills.md).

## Prüfen und aktualisieren

```sh
uv venv .venv
uv pip sync --python .venv/bin/python --require-hashes requirements.txt
.venv/bin/python -m unittest discover -s tests -v
node --test .github/scripts/*.test.cjs
docker compose config --quiet
docker build --platform linux/amd64 -t agent-desktop:dev .
bash scripts/test-container.sh agent-desktop:dev
```

Der Container-Test prüft den laufenden Desktop, CLI-Versionen, eine tatsächlich
gerenderte Chrome-Seite sowie CUA-MCP mit Fensterbeobachtung und Screenshot.
Modellanbieter-Aufrufe benötigen eigene Zugänge und gehören nicht zum
zugangsfreien Smoke-Test. Vertragstests prüfen Jev und Hindsight gegen lokale
Testserver bzw. die SDK mit Mock-Transport.

Python-Abhängigkeiten werden mit Hashes installiert, npm mit `npm ci`.
Nach bewussten Versionsänderungen die Lockfiles erneuern:

```sh
npm install --package-lock-only --ignore-scripts --no-audit --no-fund
uv pip compile requirements.in --generate-hashes --python-version 3.12 \
  --output-file requirements.txt --no-emit-index-url
```

Basis-Images und GitHub Actions sind per Digest bzw. Commit fixiert. Ubuntu-
und Chrome-Pakete kommen beim Build aus signierten Paketquellen und können sich
zwischen Builds ändern. Ein geplanter wöchentlicher Build aktualisiert diese
Systempakete auch ohne Änderung eines Manifests.

GHCR-Ziel ist `ghcr.io/ralphschuler/agent`, da der Repositoryname `.agent` nicht
als Image-Komponente verwendet wird. Builds auf `main` und `v*`-Tags publizieren
erst nach erfolgreichen Checks und dem Container-Test. Pull Requests publizieren
keine Images. Erforderliche GitHub-Einstellungen und die Merge-Regeln stehen in
[CI und automatische Updates](docs/ci.md).
