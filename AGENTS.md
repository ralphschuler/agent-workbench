# Working in this repository

Use the existing checkout; cloud tasks already run in an isolated environment.
Do not create a Git worktree unless the user explicitly asks for one.

This repository builds an Ubuntu amd64 agent desktop, not a hosted web app.
Runtime instructions live in README.md and docs/. Keep the desktop user,
DISPLAY, session D-Bus and Cua Driver consistent. Never bake API keys, CLI
authentication or browser profiles into the image. Do not disable package
signatures, TLS verification or pinned artifact hashes.

Use frozen dependencies: npm ci and pip/uv sync with requirements.txt hashes.
When changing dependencies update both manifests and lockfiles. The common
skills are under skills/; generated user configuration belongs in persistent
HOME, not the checkout. Preserve user config entries when changing the generator.

For integration changes, run `.venv/bin/python -m unittest discover -s tests -v`.
For CI policy changes also run `node --test .github/scripts/*.test.cjs`.
Validate Compose with `docker compose config --quiet`. For desktop/runtime
changes build the image and run `bash scripts/test-container.sh <image>`.
Keep model-backed checks distinct from the credential-free smoke test.
