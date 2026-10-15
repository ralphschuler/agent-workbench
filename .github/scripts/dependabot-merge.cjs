'use strict';

const BOT = 'dependabot[bot]';
const SAFE_TYPES = new Set([
  'version-update:semver-minor',
  'version-update:semver-patch',
]);

// Deliberately accept only explicit, complete Dependabot metadata. Digest-only
// updates and unfamiliar metadata stay open for a maintainer to review.
function hasOnlySafeUpdates(message) {
  const metadata = message.match(/^---\r?\n([\s\S]*?)^\.\.\.\s*$/m);
  if (!metadata || !/^updated-dependencies:\s*$/m.test(metadata[1])) return false;
  const dependencies = [...metadata[1].matchAll(/^\s*- dependency-name:\s*\S.*$/gm)];
  const types = [...metadata[1].matchAll(/^\s+update-type:\s*(\S+)\s*$/gm)];
  return dependencies.length > 0 && types.length === dependencies.length
    && types.every((match) => SAFE_TYPES.has(match[1]));
}

function eligiblePullRequest(pr, run, repository) {
  return pr.state === 'open' && !pr.draft
    && pr.user?.login === BOT
    && pr.head?.repo?.full_name === repository
    && pr.base?.repo?.full_name === repository
    && pr.base.ref === 'main'
    && pr.head.ref.startsWith('dependabot/')
    && pr.head.sha === run.head_sha
    && !/Maintainer changes/i.test(pr.body || '');
}

async function mergeDependabot({ github, context, core }) {
  const repository = `${context.repo.owner}/${context.repo.repo}`;
  const { data: run } = await github.rest.actions.getWorkflowRun({
    ...context.repo,
    run_id: context.payload.workflow_run.id,
  });
  // Re-fetch the run and PRs rather than trusting an old event or CI artifact.
  if (run.event !== 'pull_request' || run.conclusion !== 'success'
      || run.path !== '.github/workflows/ci.yml'
      || run.head_repository?.full_name !== repository) {
    core.info('This is not a successful pull request run of the repository CI.');
    return;
  }
  const associated = await github.paginate(github.rest.repos.listPullRequestsAssociatedWithCommit, {
    ...context.repo,
    commit_sha: run.head_sha,
    per_page: 100,
  });
  for (const candidate of associated) {
    const { data: pr } = await github.rest.pulls.get({
      ...context.repo,
      pull_number: candidate.number,
    });
    if (!eligiblePullRequest(pr, run, repository)) continue;

    const commits = await github.paginate(github.rest.pulls.listCommits, {
      ...context.repo,
      pull_number: pr.number,
      per_page: 100,
    });
    if (!commits.length || !commits.every((commit) =>
      commit.author?.login === BOT
      && commit.commit.verification?.verified === true
      && hasOnlySafeUpdates(commit.commit.message))) {
      core.info(`PR #${pr.number} requires manual review: commits or update types are not eligible.`);
      continue;
    }

    let result;
    try {
      // sha makes the operation atomic with respect to new commits. GitHub
      // still enforces branch protection, required checks, and review rules.
      ({ data: result } = await github.rest.pulls.merge({
        ...context.repo,
        pull_number: pr.number,
        sha: run.head_sha,
        merge_method: 'squash',
      }));
    } catch (error) {
      if (![403, 405, 409, 422].includes(error.status)) throw error;
      core.info(`PR #${pr.number} was not merged: branch rules, permissions, or head SHA prevent it (HTTP ${error.status}).`);
      continue;
    }
    core.info(result.merged
      ? `Merged verified Dependabot PR #${pr.number}.`
      : `PR #${pr.number} was not merged: ${result.message}`);
    if (result.merged) {
      // Events caused by GITHUB_TOKEN do not trigger push workflows.
      // workflow_dispatch is an explicit exception, so main still gets
      // validated and published without needing a personal access token.
      // Dispatch errors must fail visibly because the merge already happened.
      await github.rest.actions.createWorkflowDispatch({
        ...context.repo,
        workflow_id: 'ci.yml',
        ref: 'main',
      });
    }
  }
}

module.exports = mergeDependabot;
module.exports.hasOnlySafeUpdates = hasOnlySafeUpdates;
module.exports.eligiblePullRequest = eligiblePullRequest;
