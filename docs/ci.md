# Checks, dependency updates, and image publishing

`CI` validates Python integrations, the dependency merge policy, shell syntax,
and Compose configuration. Every run builds the complete `linux/amd64` desktop
image and smoke-tests desktop startup, the agent CLIs, and CUA MCP. Pull request
jobs use no registry credentials. After those checks pass, pushes to `main` and
`v*` tags publish `ghcr.io/ralphschuler/agent` with OCI labels, provenance, and an SBOM.
BuildKit caches are shared between runs. GitHub Actions use immutable commit
pins; Dependabot maintains those pins.

Manually dispatching `CI` on `main` validates and publishes the image. Dispatching
it on another branch only validates and builds, without registry credentials.
Every Monday at 05:00 UTC, a scheduled run rebuilds `main` without Docker layer
cache to refresh signed apt packages, including Chrome. It smoke-tests that
image, then reuses the freshly exported build cache for publication. Fixed base
image digests and manifest versions continue to be maintained by Dependabot.

Image tags include `main`, `latest`, and `sha-<full commit>`. A release such as
`v1.2.3` also publishes `1.2.3` and `1.2`. Deploy by digest when a fixed artifact
is required. GHCR authentication uses the publishing job's `GITHUB_TOKEN`; no
personal access token is needed. The image name intentionally omits the leading
dot in the Git repository name.

## Repository settings

These GitHub settings need a repository administrator; files in this checkout
cannot activate them:

1. Enable GitHub Actions and permit the pinned actions used by the workflows.
   Allow the publishing job to write packages.
2. Enable squash merging.
3. Protect `main` with a branch rule or ruleset requiring a pull request and the
   **Required checks** check from the **CI** workflow. Require branches to be up
   to date before merging. Do not give the GitHub Actions app a bypass.
4. Enable Dependabot alerts and security updates in the repository's security
   settings. Scheduled version updates are configured in
   [dependabot.yml](../.github/dependabot.yml) for Actions, Docker, npm, and pip.
5. For a pre-existing GHCR package, grant this repository Actions write access.
   Choose the package's visibility after the first publication; it may initially
   be private. Protect release tags with a ruleset if releases need approval.

Required reviews remain respected. The dependency workflow does not approve PRs
or bypass branch protection. After any required human review or other external
check completes, rerun `CI` to retry a previously blocked merge. Repository
auto-merge does not need to be enabled: the workflow performs a normal squash
merge only after the successful CI run.

## Dependabot merge policy

Weekly minor and patch updates are grouped separately for each ecosystem. Major
updates remain available as individual PRs for manual review.

The merge workflow runs from the default branch after `CI` succeeds. It never
checks out pull request code or consumes CI artifacts with its write token. It
re-fetches the run and PR, verifies the repository, target branch, author, and
tested head SHA, and requires **every** commit to have a verified Dependabot
signature and explicit minor/patch update metadata. Unknown metadata, digest-only
updates without that metadata, and maintainer changes remain for manual review.
The merge API checks the SHA atomically and enforces GitHub branch rules.
After a successful merge it dispatches `CI` on `main` to validate and publish the
updated image. This explicit dispatch is necessary because merges performed
with `GITHUB_TOKEN` do not trigger another push workflow. Only the merge job has
Actions write permission; it uses no additional credential.

Changing a dependency also has to pass the complete container build. Python
requirements must retain valid hashes; regenerate the project's requirements
lock when a change needs it rather than disabling hash verification.

## Local checks

```sh
python3 -m unittest discover -s tests -v
node --test .github/scripts/*.test.cjs
for script in scripts/*.sh; do bash -n "$script"; done
docker compose config --quiet
docker build --platform linux/amd64 -t agent:dev .
bash scripts/test-container.sh agent:dev
```

GitHub-hosted CI and GHCR publication can only be verified after these files are
pushed. Local checks do not change repository settings or publish an image.
