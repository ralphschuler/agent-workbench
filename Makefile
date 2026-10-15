SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

DEV_IMAGE ?= agent-workbench:dev
MEMORY ?= 0
SNAPSHOT ?= latest
WORKBENCH_SOURCE := $(CURDIR)
export DEV_IMAGE SNAPSHOT WORKBENCH_SOURCE

COMPOSE := docker compose
ifeq ($(MEMORY),1)
COMPOSE += --profile memory
endif

.PHONY: help init up down build status logs shell codex claude github-login memory-login doctor test smoke backup-init backup snapshots backup-check backup-test restore

help: ## Verfügbare Befehle anzeigen
	@awk 'BEGIN {FS = ":.*## "; printf "Agent Workbench\n\n"} /^[a-z][a-z-]*:.*## / {printf "  make %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init: ## Lokale Konfiguration anlegen; bestehende Dateien erhalten
	@bash scripts/init.sh

up: init ## Desktop bauen und starten; MEMORY=1 aktiviert Hindsight
	$(COMPOSE) up --build -d

down: ## Dienste beenden; Volumes bleiben erhalten
	docker compose --profile memory down

build: ## Lokales Entwicklungsimage bauen (DEV_IMAGE überschreibbar)
	docker build --platform linux/amd64 -t "$$DEV_IMAGE" .

status: ## Status aller Dienste anzeigen
	docker compose --profile memory ps

logs: ## Dienstprotokolle verfolgen
	docker compose --profile memory logs --follow --tail 100

shell: ## Terminal im laufenden Agent-Container öffnen
	docker compose exec agent bash

codex: ## Codex im laufenden Desktop starten
	docker compose exec agent codex

claude: ## Claude Code im laufenden Desktop starten
	docker compose exec agent claude

github-login: ## GitHub-Anmeldung für gh und GitHub MCP
	docker compose exec agent gh auth login --hostname github.com --git-protocol https --web

memory-login: ## Separate Codex-Anmeldung für Hindsight einrichten
	python3 scripts/hindsight-login.py

doctor: ## Dienststatus und vorhandenen Desktop-Healthcheck prüfen
	docker compose --profile memory ps
	docker compose exec -T agent python3 /opt/agent/scripts/healthcheck.py

test: build ## Python-/Policy-Tests im Image und Compose-Prüfung ausführen
	docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true \
		--mount "type=bind,src=$$WORKBENCH_SOURCE,dst=/source,readonly" --workdir /source \
		--env PYTHONDONTWRITEBYTECODE=1 --entrypoint /bin/bash "$$DEV_IMAGE" \
		-c 'python -m unittest discover -s tests -v && node --test .github/scripts/*.test.cjs'
	docker compose --profile memory config --quiet

smoke: ## Lokales Image mit Desktop, Browser-MCP und CUA live prüfen
	bash scripts/test-container.sh "$$DEV_IMAGE"

backup-init: ## Verschlüsseltes Restic-Repository und lokalen Schlüssel anlegen
	python3 scripts/backup.py init

backup: ## Konsistente verschlüsselte Sicherung erstellen
	python3 scripts/backup.py backup

snapshots: ## Vorhandene Sicherungen anzeigen
	python3 scripts/backup.py snapshots

backup-check: ## Integrität des Backup-Repositorys prüfen
	python3 scripts/backup.py check

backup-test: ## Isolierten echten Backup-/Restore-Rundlauf prüfen
	bash scripts/test-backup.sh

restore: ## SNAPSHOT=latest in eine leere Zielumgebung wiederherstellen
	python3 scripts/backup.py restore --snapshot "$$SNAPSHOT"
