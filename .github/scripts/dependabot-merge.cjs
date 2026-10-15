'use strict';

const BOT = 'dependabot[bot]';
const ACTIONS_BOT = 'github-actions[bot]';
const WORKFLOW = '.github/workflows/ci.yml';
const SAFE_TYPES = new Set(['version-update:semver-minor', 'version-update:semver-patch']);
const REQUIRED_JOBS = ['Validation', 'Backup and restore', 'Container build', 'Required checks'];
const TERMINAL_FAILURES = new Set(['failure', 'timed_out', 'action_required', 'startup_failure']);

function hasOnlySafeUpdates(message) {
  const metadata = message.match(/^---\r?\n([\s\S]*?)^\.\.\.\s*$/m);
  if (!metadata || !/^updated-dependencies:\s*$/m.test(metadata[1])) return false;
  const dependencies = [...metadata[1].matchAll(/^\s*- dependency-name:\s*\S.*$/gm)];
  const types = [...metadata[1].matchAll(/^\s+update-type:\s*(\S+)\s*$/gm)];
  return dependencies.length > 0 && types.length === dependencies.length
    && types.every((match) => SAFE_TYPES.has(match[1]));
}

function eligiblePullRequest(pr, repository) {
  return pr.state === 'open' && !pr.draft && pr.user?.login === BOT
    && pr.head?.repo?.full_name === repository && pr.base?.repo?.full_name === repository
    && pr.base.ref === 'main' && pr.head.ref.startsWith('dependabot/')
    && !/Maintainer changes/i.test(pr.body || '');
}

function latestRun(runs, repository, branch, sha, main = false) {
  const timestamp = (run) => {
    const times = [run.run_started_at, run.updated_at].map((value) => Date.parse(value)).filter(Number.isFinite);
    return times.length ? Math.max(...times) : null;
  };
  return runs.filter((run) => run.path === WORKFLOW
    && run.head_repository?.full_name === repository && run.head_branch === branch
    && run.head_sha === sha
    && (main ? ['push', 'workflow_dispatch', 'schedule'] : ['pull_request', 'workflow_dispatch']).includes(run.event))
    .sort((a, b) => {
      if (a.id === b.id) return (b.run_attempt || 1) - (a.run_attempt || 1);
      const aTime = timestamp(a), bTime = timestamp(b);
      return aTime !== null && bTime !== null && aTime !== bTime ? bTime - aTime : b.id - a.id;
    })[0];
}

async function mergeDependabot({ github, context, core }) {
  const repo = context.repo;
  const repository = `${repo.owner}/${repo.repo}`;
  const info = (message) => core.info(message);
  const mainSha = async () => (await github.rest.git.getRef({ ...repo, ref: 'heads/main' })).data.object.sha;
  const main = await mainSha();
  const compare = async (base, head) => (await github.rest.repos.compareCommitsWithBasehead({
    ...repo, basehead: `${base}...${head}`,
  })).data;
  const ancestor = async (base, head) => {
    if (base === head) return true;
    const result = await compare(base, head);
    return result.behind_by === 0 && result.merge_base_commit?.sha === base
      && ['ahead', 'identical'].includes(result.status);
  };
  const runsFor = async (branch, sha, isMain = false) => latestRun(
    await github.paginate(github.rest.actions.listWorkflowRuns, {
      ...repo, workflow_id: 'ci.yml', branch, head_sha: sha, per_page: 100,
    }), repository, branch, sha, isMain,
  );
  const successful = async (run, publication = false) => {
    const { data: current } = await github.rest.actions.getWorkflowRun({ ...repo, run_id: run.id });
    if (current.status !== 'completed' || current.conclusion !== 'success'
        || current.head_sha !== run.head_sha || current.run_attempt !== run.run_attempt
        || current.path !== WORKFLOW || current.head_repository?.full_name !== repository) return false;
    const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRun, {
      ...repo, run_id: run.id, filter: 'latest', per_page: 100,
    });
    return [...REQUIRED_JOBS, ...(publication ? ['Publish GHCR image'] : [])].every((name) =>
      jobs.some((job) => job.name === name && job.status === 'completed' && job.conclusion === 'success'));
  };
  const dispatch = async (ref) => github.rest.actions.createWorkflowDispatch({ ...repo, workflow_id: 'ci.yml', ref });

  // A full scan reconstructs the queue even after lost/replaced pending events.
  const candidates = (await github.paginate(github.rest.pulls.list, {
    ...repo, state: 'open', base: 'main', per_page: 100, sort: 'created', direction: 'asc',
  })).filter((pr) => eligiblePullRequest(pr, repository)).sort((a, b) => a.number - b.number);
  const mainRun = await runsFor('main', main, true);
  if (!mainRun || (mainRun.status === 'completed' && mainRun.conclusion === 'cancelled')) {
    if (await mainSha() !== main) { info('Main changed during the scan; wait for the next tick.'); return; }
    await dispatch('main');
    info('Requested missing CI/publication for current main; PR queue is paused.');
    return;
  }
  if (!await successful(mainRun, true)) {
    info('Current main has not completed CI including GHCR publication; PR queue is paused.');
    return;
  }
  if (!candidates.length) { info('No eligible-source Dependabot PRs are open.'); return; }

  async function verifiedCommits(pr) {
    const commits = await github.paginate(github.rest.pulls.listCommits, {
      ...repo, pull_number: pr.number, per_page: 100,
    });
    if (!commits.length || commits.at(-1).sha !== pr.head.sha) return false;
    let previous;
    let updates = 0;
    for (const commit of commits) {
      if (!commit.commit.verification?.verified || !Array.isArray(commit.parents)) return false;
      const parents = commit.parents.map((parent) => parent.sha);
      if (previous && parents[0] !== previous) return false;
      if (!previous && (!parents[0] || !await ancestor(parents[0], main))) return false;
      if (commit.author?.login === BOT
          && parents.length === 1 && hasOnlySafeUpdates(commit.commit.message)) {
        updates += 1;
      } else if (commit.author?.login === ACTIONS_BOT
          && [ACTIONS_BOT, 'web-flow'].includes(commit.committer?.login)
          && parents.length === 2 && previous && parents[0] !== parents[1]
          && await ancestor(parents[1], main)) {
        // Only verified base syncs are exempt from dependency metadata:
        // parent one continues this PR; parent two is an ancestor of main.
      } else return false;
      previous = commit.sha;
    }
    return updates > 0;
  }

  async function unchanged(pr) {
    const { data: fresh } = await github.rest.pulls.get({ ...repo, pull_number: pr.number });
    return eligiblePullRequest(fresh, repository) && fresh.head.sha === pr.head.sha
      && fresh.mergeable === true && await mainSha() === main;
  }

  for (const candidate of candidates) {
    const { data: pr } = await github.rest.pulls.get({ ...repo, pull_number: candidate.number });
    if (!eligiblePullRequest(pr, repository) || !await verifiedCommits(pr)) {
      info(`PR #${candidate.number} requires manual review: source, signatures, history, or metadata.`);
      continue;
    }
    if (pr.mergeable === false) {
      info(`PR #${pr.number} has a merge conflict; leaving it for a maintainer and scanning later PRs.`);
      continue;
    }
    if (pr.mergeable !== true) {
      info(`PR #${pr.number} mergeability is not yet known; keep the queue on this candidate.`);
      return;
    }
    const run = await runsFor(pr.head.ref, pr.head.sha);
    if (run && run.status !== 'completed') {
      info(`PR #${pr.number} CI is queued/running; keep the queue on this candidate.`);
      return;
    }
    const comparison = await compare(main, pr.head.sha);
    const upToDate = comparison.behind_by === 0 && comparison.merge_base_commit?.sha === main
      && comparison.status === 'ahead';
    if (!upToDate) {
      if (!await unchanged(pr)) { info('PR head or main changed; wait for the next tick.'); return; }
      try {
        await github.rest.pulls.updateBranch({ ...repo, pull_number: pr.number, expected_head_sha: pr.head.sha });
      } catch (error) {
        if (![403, 405, 409, 422].includes(error.status)) throw error;
        info(`PR #${pr.number} could not be updated (HTTP ${error.status}); leaving it for review.`);
        return;
      }
      // updateBranch is asynchronous. Dispatch only after its verified new
      // head is visible; the scheduled full scan recovers delayed updates.
      const { data: updated } = await github.rest.pulls.get({ ...repo, pull_number: pr.number });
      if (eligiblePullRequest(updated, repository) && updated.head.sha !== pr.head.sha
          && await mainSha() === main && await verifiedCommits(updated)
          && await ancestor(main, updated.head.sha)) await dispatch(updated.head.ref);
      info(`Requested base update for PR #${pr.number}; no other PR is mutated this tick.`);
      return;
    }
    if (run && TERMINAL_FAILURES.has(run.conclusion)) {
      info(`PR #${pr.number} has failed current-head CI; leaving it for review and scanning later PRs.`);
      continue;
    }
    if (!run || run.conclusion === 'cancelled') {
      if (!await unchanged(pr)) { info('PR head or main changed; wait for the next tick.'); return; }
      await dispatch(pr.head.ref);
      info(`Requested missing/cancelled CI for PR #${pr.number}; wait before considering another PR.`);
      return;
    }
    if (!await successful(run)) {
      info(`PR #${pr.number} lacks successful required jobs; leaving it for manual review.`);
      return;
    }
    if (!await unchanged(pr)) { info('PR head or main changed; wait for the next tick.'); return; }
    let result;
    try {
      // The expected head is atomic. Strict up-to-date branch protection
      // closes the REST API's external/manual base-update race.
      ({ data: result } = await github.rest.pulls.merge({
        ...repo, pull_number: pr.number, sha: pr.head.sha, merge_method: 'squash',
      }));
    } catch (error) {
      if (![403, 405, 409, 422].includes(error.status)) throw error;
      info(`PR #${pr.number} was not merged (HTTP ${error.status}); branch rules and SHA checks remain enforced.`);
      return;
    }
    if (result.merged) {
      // GITHUB_TOKEN push events do not start CI; explicit dispatch does.
      // Failure remains visible; a later main gate recovers a missing run.
      await dispatch('main');
      info(`Merged PR #${pr.number} and requested main CI/publication.`);
    } else info(`PR #${pr.number} was not merged: ${result.message}`);
    return;
  }
}

module.exports = mergeDependabot;
module.exports.hasOnlySafeUpdates = hasOnlySafeUpdates;
module.exports.eligiblePullRequest = eligiblePullRequest;
module.exports.latestRun = latestRun;
