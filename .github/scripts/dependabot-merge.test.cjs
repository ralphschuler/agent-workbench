'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const merge = require('./dependabot-merge.cjs');

const repository = 'ralphschuler/agent-workbench';
const head = 'a'.repeat(40);
const metadata = (...types) => `Bump dependencies\n\n---\nupdated-dependencies:\n${types.map((type, index) =>
  `- dependency-name: example-${index}\n  update-type: ${type}`).join('\n')}\n...\n`;
const safeMessage = metadata('version-update:semver-patch');
const makePr = () => ({
  number: 7, state: 'open', draft: false, body: '',
  user: { login: 'dependabot[bot]' },
  head: { repo: { full_name: repository }, ref: 'dependabot/pip/example-1.2.3', sha: head },
  base: { repo: { full_name: repository }, ref: 'main' },
});
const makeCommit = () => ({
  author: { login: 'dependabot[bot]' },
  commit: { message: safeMessage, verification: { verified: true } },
});

function makeApi({ pr = makePr(), commits = [makeCommit()], run = {}, mergeError, dispatchError } = {}) {
  const calls = [];
  const dispatches = [];
  const listPullRequests = Symbol('associated PRs');
  const listCommits = Symbol('commits');
  return {
    calls,
    dispatches,
    context: { repo: { owner: 'ralphschuler', repo: 'agent-workbench' }, payload: { workflow_run: { id: 42 } } },
    core: { info() {} },
    github: {
      rest: {
        actions: {
          getWorkflowRun: async () => ({ data: {
            event: 'pull_request', conclusion: 'success', path: '.github/workflows/ci.yml',
            head_sha: head, head_repository: { full_name: repository }, ...run,
          } }),
          createWorkflowDispatch: async (args) => {
            dispatches.push(args);
            if (dispatchError) throw dispatchError;
          },
        },
        repos: { listPullRequestsAssociatedWithCommit: listPullRequests },
        pulls: {
          get: async () => ({ data: pr }),
          listCommits,
          merge: async (args) => {
            calls.push(args);
            if (mergeError) throw mergeError;
            return { data: { merged: true } };
          },
        },
      },
      paginate: async (method) => method === listPullRequests ? [{ number: pr.number }] : commits,
    },
  };
}

test('only complete explicit patch/minor metadata is eligible', () => {
  assert.equal(merge.hasOnlySafeUpdates(metadata('version-update:semver-minor', 'version-update:semver-patch')), true);
  for (const message of [
    metadata('version-update:semver-major'),
    metadata('version-update:semver-patch', 'version-update:semver-major'),
    metadata('unknown'),
    'Bump a dependency to 1.2.3',
    safeMessage.replace('  update-type: version-update:semver-patch\n', ''),
    safeMessage.replace('...\n', ''),
    safeMessage.replace('updated-dependencies:', 'other-data:'),
  ]) assert.equal(merge.hasOnlySafeUpdates(message), false, message);
});

test('merges a signed Dependabot patch with the tested head SHA and squash method', async () => {
  const api = makeApi();
  await merge(api);
  assert.deepEqual(api.calls, [{ owner: 'ralphschuler', repo: 'agent-workbench', pull_number: 7, sha: head, merge_method: 'squash' }]);
  assert.deepEqual(api.dispatches, [{ owner: 'ralphschuler', repo: 'agent-workbench', workflow_id: 'ci.yml', ref: 'main' }]);
});

test('rejects failed, foreign, and unrelated CI runs', async () => {
  for (const run of [
    { conclusion: 'failure' }, { event: 'push' }, { path: '.github/workflows/other.yml' },
    { head_repository: { full_name: 'attacker/fork' } },
  ]) {
    const api = makeApi({ run });
    await merge(api);
    assert.equal(api.calls.length, 0);
  }
});

test('rejects stale heads, forks, changed targets, drafts, authors, and maintainer changes', async () => {
  const variants = [
    (pr) => { pr.head.sha = 'b'.repeat(40); },
    (pr) => { pr.head.repo.full_name = 'attacker/fork'; },
    (pr) => { pr.base.ref = 'other'; },
    (pr) => { pr.draft = true; },
    (pr) => { pr.state = 'closed'; },
    (pr) => { pr.user.login = 'someone'; },
    (pr) => { pr.head.ref = 'other-branch'; },
    (pr) => { pr.body = 'Maintainer changes'; },
  ];
  for (const change of variants) {
    const pr = makePr();
    change(pr);
    const api = makeApi({ pr });
    await merge(api);
    assert.equal(api.calls.length, 0);
  }
});

test('every commit must be signed by Dependabot with safe metadata', async () => {
  const unsigned = makeCommit();
  unsigned.commit.verification.verified = false;
  const otherAuthor = makeCommit();
  otherAuthor.author.login = 'someone';
  const major = makeCommit();
  major.commit.message = metadata('version-update:semver-major');
  for (const commits of [[], [unsigned], [otherAuthor], [makeCommit(), major]]) {
    const api = makeApi({ commits });
    await merge(api);
    assert.equal(api.calls.length, 0);
  }
});

test('respects GitHub branch protection and SHA race rejections', async () => {
  for (const status of [403, 405, 409, 422]) {
    const api = makeApi({ mergeError: { status } });
    await assert.doesNotReject(merge(api));
    assert.equal(api.dispatches.length, 0);
  }
  await assert.rejects(merge(makeApi({ mergeError: Object.assign(new Error('server error'), { status: 500 }) })), /server error/);
});

test('reports publication dispatch failure after a merge', async () => {
  const api = makeApi({ dispatchError: Object.assign(new Error('dispatch denied'), { status: 403 }) });
  await assert.rejects(merge(api), /dispatch denied/);
  assert.equal(api.calls.length, 1);
  assert.equal(api.dispatches.length, 1);
});
