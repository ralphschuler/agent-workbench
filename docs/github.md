# GitHub CLI und MCP

Agent Workbench enthält die offizielle `gh`-CLI und den offiziellen lokalen
[GitHub MCP Server](https://github.com/github/github-mcp-server). Codex und Claude
starten dessen natives Programm über stdio. Dafür sind weder ein zusätzlicher
Dienst noch ein Docker-Socket im Desktop nötig.

Im Desktop mit dem eigenen GitHub-Konto anmelden:

```bash
docker compose exec agent gh auth login --hostname github.com
docker compose exec agent gh auth status --hostname github.com
```

Die von `gh` verwaltete Anmeldung liegt im persistenten Benutzerprofil bzw.
Credential Store. Der MCP-Wrapper verwendet diese Anmeldung, ohne ihren Token in
der MCP-Konfiguration abzulegen. `gh` und der Server erhalten nur die Rechte,
die das angemeldete Konto bzw. sein Token tatsächlich besitzt. Das Bereitstellen
von Werkzeugen erteilt keine zusätzlichen Repository-Rechte.

## Authentifizierung zur Laufzeit

`scripts/github-mcp.py` prüft der Reihe nach:

1. `GITHUB_PERSONAL_ACCESS_TOKEN`
2. `GH_TOKEN`
3. `GITHUB_TOKEN`
4. Die Ausgabe von `gh auth token --hostname github.com`

Ein expliziter Token hat Vorrang vor der gespeicherten `gh`-Anmeldung. Der
Fallback wartet höchstens fünf Sekunden und fängt stdout/stderr ab. Anschließend
startet der Wrapper `/usr/local/bin/github-mcp-server stdio`; der Token wird nur
in dessen Prozessumgebung als `GITHUB_PERSONAL_ACCESS_TOKEN` übergeben, nie als
Argument oder Protokollausgabe. Der Original-Server kommuniziert direkt mit
GitHub. Diese Integration ist auf `github.com` festgelegt.

Ohne Anmeldung beendet sich der Wrapper mit Exitcode `1`, leerem stdout und
einem klaren Hinweis auf stderr. Es erfolgt keine automatische Kontoanmeldung.
Die MCP-Konfiguration enthält nur den Wrapper-Aufruf und die Namen weitergereichter
Umgebungsvariablen. Tokens gehören weder in das Image noch in eingecheckte
Konfigurationsdateien. Die Persistenz der eigentlichen CLI-Anmeldung verwaltet `gh`.

## Repositorys und Build-Workflows

Standardmäßig sind diese offiziellen Toolsets aktiv:

```text
context,repos,issues,pull_requests,users,actions
```

Das entspricht den fünf Upstream-Standardgruppen plus `actions`. Damit sind
Repository-/PR-Arbeit sowie Workflow-Abfragen, Job-Logs und Workflow-Steuerung
verfügbar, soweit die Anmeldung die benötigten Rechte besitzt. `all` und zusätzliche
Verwaltungsgruppen werden nicht pauschal aktiviert. Bei Bedarf lässt sich die
Auswahl mit `GITHUB_TOOLSETS` ausdrücklich ändern; Codex reicht diese Variable an
den Wrapper weiter.

## Fixierung und Prüfung

Der native Server ist auf **v2.0.2** fixiert, Quell-Commit
`eb47a99ddb866ca2b8a162920e6bda9521f33ebb`. Der Build lädt das offizielle
Linux-amd64-Archiv und prüft vor dem Entpacken diesen veröffentlichten SHA-256:

```text
815e27d931c796af0c97f24fd211e14f8a4607d4528377719a5ad2352086b0b0
```

Die MIT-Lizenz wird nach
`/usr/local/share/licenses/github-mcp-server/LICENSE` übernommen. Der Download
verwendet HTTPS und die normale Zertifikatsprüfung. Herkunft und erwarteter Hash
stehen in `docker/install-github-mcp.sh`:

- [Offizielles Release v2.0.2](https://github.com/github/github-mcp-server/releases/tag/v2.0.2)
- [Offizielle Prüfsummen](https://github.com/github/github-mcp-server/releases/download/v2.0.2/github-mcp-server_2.0.2_checksums.txt)
- [Offizielle stdio-, Auth- und Toolset-Dokumentation](https://github.com/github/github-mcp-server/blob/eb47a99ddb866ca2b8a162920e6bda9521f33ebb/README.md)

Der echte Download, seine Prüfsumme und `github-mcp-server --version` wurden
geprüft. Lokale Tests prüfen Token-Priorität, Wiederverwendung der CLI-Anmeldung,
Timeouts, fehlende Anmeldung und Schutz vor Token-Ausgaben. Diese Tests benötigen
keine echten GitHub-Zugangsdaten:

```bash
.venv/bin/python -m unittest discover -s tests -p test_github_mcp.py -v
```

Ein zusätzlicher Test mit dem echten Server bestätigte MCP-Initialisierung und
die Erkennung von 48 Werkzeugen, einschließlich der Actions-Workflow-Steuerung.
Dafür wurden ausschließlich ein Testwert und ein unerreichbares Loopback-Ziel
für API und Proxy verwendet; es gab keine authentifizierte GitHub-Anfrage und
keinen Werkzeugaufruf gegen GitHub.

Ein bestandener lokaler Test belegt keine Anmeldung und keine Schreibrechte auf
GitHub. Die Einrichtung führt keine GitHub-Schreiboperationen aus. Konto- und
Repository-Berechtigungen werden erst durch echte, gezielte Aufrufe mit der
eigenen Anmeldung bestätigt.
