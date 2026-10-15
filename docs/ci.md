# Checks, dependency updates, and image publishing

`CI` validates Python integrations, the dependency merge policy, shell syntax,
and Compose configuration. Every run builds the complete `linux/amd64` desktop
image and smoke-tests desktop startup, the agent CLIs, CUA and Playwright MCP.
Pull request jobs use no registry credentials. A separate **Backup and restore** job runs the
isolated encrypted Restic roundtrip on every event, alongside the desktop build.
After all those checks pass, pushes to `main` and
`v*` tags publish `ghcr.io/ralphschuler/agent-workbench` with OCI labels,
provenance, and an SBOM.
For `v*` tag pushes, a separate job then creates a GitHub Release with generated
release notes. It runs only after validation, backup/restore, the container
smoke test, and GHCR publication succeed. **Required checks** also requires that
release job to pass.
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
personal access token is needed. The image uses the Agent Workbench product
name independently of the GitHub repository name. Source links and GitHub
Releases use the repository `ralphschuler/agent-workbench`.

Use semantic version tags such as `v1.2.3` for releases and matching versioned
image tags. The workflow accepts any `v*` tag consistently with its existing
image-publication trigger; it does not enforce semantic version syntax. Release
creation verifies that the pushed tag already exists. Re-running a tag workflow
preserves any existing release and its notes, including manually edited notes;
it does not overwrite or republish that release. Only the release job receives
repository contents write permission for this step, using `GITHUB_TOKEN`.

## Repository settings

These GitHub settings need a repository administrator; files in this checkout
cannot activate them:

1. Enable GitHub Actions and permit the pinned actions used by the workflows.
   Allow the publishing job to write packages.
2. Enable squash merging.
3. Protect `main` with a branch rule or ruleset requiring a pull request and the
   **Required checks** check from the **CI** workflow, restricted to the
   **GitHub Actions** source. Require branches to be up to date before merging.
   Do not give the GitHub Actions app a bypass.
4. Enable Dependabot alerts and security updates in the repository's security
   settings. Scheduled version updates are configured in
   [dependabot.yml](../.github/dependabot.yml) for Actions, Docker, npm, and pip.
5. For a pre-existing GHCR package, grant this repository Actions write access.
   Choose the package's visibility after the first publication; it may initially
   be private. Protect release tags with a ruleset if releases need approval.

Required reviews remain respected. The dependency workflow does not approve PRs
or bypass branch protection. After any required human review or other external
check completes, the next controller tick can retry the merge. Dispatch
**Merge Dependabot updates** manually to request an earlier tick. Repository
auto-merge does not need to be enabled: the controller performs a normal squash
merge only after the successful CI run.

## Dependabot merge policy

Weekly minor and patch updates are grouped separately for each ecosystem. Major
updates remain available as individual PRs for manual review.

The controller runs from the default branch after every completed `CI` run,
including failed runs and main publication, on manual dispatch, and every five
minutes as recovery. Scheduled execution can be delayed by GitHub. It never
checks out pull request code or consumes CI artifacts with its write token.
A repository-wide concurrency group with cancellation disabled serializes
controller ticks.

Each tick lists **all** open PRs and considers eligible Dependabot updates in
ascending PR-number order. It first requires successful CI for the current
immutable `main` SHA, including validation, backup/restore, container smoke,
**Publish GHCR image**, and **Required checks**. Missing or cancelled main CI is
dispatched again; queued, running, or failed main CI pauses the PR queue. This
also recovers a lost post-merge dispatch after the last PR has closed.

The oldest eligible candidate keeps its place while its current-head CI is
queued or running. A missing or cancelled run gets one explicit branch CI
dispatch, then the tick returns. Definitively failed unchanged PR CI remains
for a maintainer; the controller may consider a later candidate. A confirmed
merge conflict is also skipped without a branch update, so it cannot block
independent PRs indefinitely. Unknown mergeability, unknown results, and
incomplete required checks hold the candidate rather than silently advancing
the queue. No unchanged failed build is automatically rerun.

Before merging, the candidate must contain current `main` as an ancestor and
have successful CI for its exact current head, from the repository's `ci.yml`.
An out-of-date branch is updated with GitHub's `updateBranch` API and
`expected_head_sha`. The update can complete asynchronously: CI is dispatched
only once the new verified head is visible; a later tick recovers delayed
completion. Each tick performs at most one PR update or merge and returns after
its associated CI dispatch. It does not update another branch while waiting.

Ordinary dependency commits still require a GitHub-verified Dependabot author,
one parent, and complete explicit minor/patch metadata. Major updates, unknown
metadata, digest-only updates without that metadata, and maintainer changes
stay for manual review. GitHub may use `web-flow` as the committer when signing
a genuine Dependabot commit; the verified Dependabot author is the identity
required by this policy.

There is one narrow history exception for API base updates: a GitHub-verified
`github-actions[bot]` author, an Actions or `web-flow` committer, exactly two
parents, a first parent continuing the verified PR chain, and a second parent
that is an ancestor of current `main`. Other merge commits are rejected. These
checks trust repository-owned Actions signing identity and constrain ancestry;
they do **not** independently reproduce the merge tree. The controller already
has repository write permissions, so its default-branch code and other
repository-owned Actions remain part of that trust boundary.

Immediately before a PR mutation, both its head and `main` are fetched again.
The update/merge API atomically checks the expected head SHA. GitHub's merge API
does not accept an expected base SHA: an external manual merge can still race
the final base read. The strict up-to-date branch protection described above is
required to close that gap; no force operation or protection bypass is used.

After a successful merge the controller dispatches `CI` on `main` to validate
and publish the updated image, then returns. It waits for that publication
before advancing another PR. Explicit branch and main dispatches are necessary
because `GITHUB_TOKEN` updates do not trigger ordinary push workflows. The
controller uses the repository token; it needs no additional user credential.

All Dependabot branch CI runs, whether opened as PRs or explicitly dispatched,
share a separate global concurrency group with cancellation disabled. GitHub
concurrency is **not a durable FIFO queue**: a newer pending run can replace an
older pending run even when running jobs are not cancelled. Full scans and the
scheduled recovery therefore remain necessary. Stable candidate ordering and
returning while its run is pending avoid dispatching later candidates in
parallel. Normal PRs keep their existing cancellation behavior.

Changing a dependency also has to pass the complete container build. Python
requirements must retain valid hashes; regenerate the project's requirements
lock when a change needs it rather than disabling hash verification.

## Backup integration check

`bash scripts/test-backup.sh` uses disposable Compose projects and fixture data.
It checks cold backup/restart, encrypted restoration, preserved data ownership
and permissions, configuration recovery, recovery-key exclusion, wrong-key and
tamper detection, and refusal to overwrite existing destination data. It needs
Docker Engine, Compose, and Python 3 without additional Python packages.

The test uses the official Restic image pinned by digest in `scripts/backup.py`.
It only needs registry access to pull that image when absent; fixture services
and backup helpers run with networking disabled. It requires no provider keys,
registry login, or access to production backups. **Required checks**, GHCR
publication, and release creation all require this job to succeed.

## Local checks

```sh
python3 -m unittest discover -s tests -v
node --test .github/scripts/*.test.cjs
for script in scripts/*.sh; do bash -n "$script"; done
docker compose config --quiet
bash scripts/test-backup.sh
docker build --platform linux/amd64 -t agent-workbench:dev .
bash scripts/test-container.sh agent-workbench:dev
```

GitHub-hosted CI, GHCR publication, and GitHub Release creation can only be
verified after these files are pushed. Local checks do not change repository
settings, publish an image, or create a release.
