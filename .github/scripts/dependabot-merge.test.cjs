'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const controller = require('./dependabot-merge.cjs');
const repository = 'ralphschuler/agent-workbench';
const main = 'a'.repeat(40), head = 'b'.repeat(40), old = 'c'.repeat(40), synced = 'd'.repeat(40);
const bot = 'dependabot[bot]', actions = 'github-actions[bot]';
const metadata = (...types) => `Bump dependencies\n\n---\nupdated-dependencies:\n${types.map((type, i) => `- dependency-name: example-${i}\n  update-type: ${type}`).join('\n')}\n...\n`;
const safe = metadata('version-update:semver-patch');
function pr(number = 7, sha = head) {
  return { number, state: 'open', draft: false, mergeable: true, body: '', user: { login: bot },
    head: { repo: { full_name: repository }, ref: `dependabot/pip/example-${number}`, sha },
    base: { repo: { full_name: repository }, ref: 'main', sha: main } };
}
function commit(sha = head, parent = main) {
  return { sha, author: { login: bot }, committer: { login: 'web-flow' }, parents: [{ sha: parent }],
    commit: { message: safe, verification: { verified: true } } };
}
function syncCommit() {
  return { sha: synced, author: { login: actions }, committer: { login: 'web-flow' },
    parents: [{ sha: head }, { sha: main }], commit: { message: 'Merge main into dependabot branch', verification: { verified: true } } };
}
function run(id, branch, sha, extra = {}) {
  return { id, run_attempt: 1, event: branch === 'main' ? 'push' : 'pull_request',
    head_branch: branch, head_sha: sha, path: '.github/workflows/ci.yml',
    head_repository: { full_name: repository }, status: 'completed', conclusion: 'success', ...extra };
}
function api(options = {}) {
  const prs = options.prs || [pr()];
  const runs = options.runs || [run(100, 'main', main), ...prs.map((p, i) => run(200 + i, p.head.ref, p.head.sha))];
  const commits = options.commits || Object.fromEntries(prs.map((p) => [p.number, [commit(p.head.sha)]]));
  const operations = [], dispatches = [], gets = {}, logs = [], scans = [];
  let mainReads = 0;
  const methods = Object.fromEntries(['prs', 'commits', 'runs', 'jobs'].map((name) => [name, Symbol(name)]));
  const state = { prs, runs, commits, operations, dispatches, scans, gets, logs };
  return { ...state,
    context: { repo: { owner: 'ralphschuler', repo: 'agent-workbench' }, payload: {} },
    core: { info: (message) => logs.push(message) },
    github: {
      rest: {
        git: { getRef: async () => ({ data: { object: { sha: options.mainReads?.[mainReads++] || main } } }) },
        repos: { compareCommitsWithBasehead: async ({ basehead }) => {
          const [base] = basehead.split('...');
          return { data: options.comparisons?.[basehead] || { status: 'ahead', behind_by: 0, merge_base_commit: { sha: base } } };
        } },
        actions: {
          listWorkflowRuns: methods.runs, listJobsForWorkflowRun: methods.jobs,
          getWorkflowRun: async ({ run_id }) => ({ data: { ...runs.find((r) => r.id === run_id), ...options.currentRun } }),
          createWorkflowDispatch: async (args) => { dispatches.push(args); if (options.dispatchError) throw options.dispatchError; },
        },
        pulls: {
          list: methods.prs, listCommits: methods.commits,
          get: async ({ pull_number }) => {
            gets[pull_number] = (gets[pull_number] || 0) + 1;
            const current = structuredClone(prs.find((p) => p.number === pull_number));
            return { data: options.prRead ? options.prRead(current, gets[pull_number]) : current };
          },
          updateBranch: async (args) => { operations.push({ type: 'update', ...args }); if (options.updateError) throw options.updateError; options.afterUpdate?.(state); },
          merge: async (args) => { operations.push({ type: 'merge', ...args }); if (options.mergeError) throw options.mergeError; return { data: { merged: options.merged !== false } }; },
        },
      },
      paginate: async (method, args) => {
        if (method === methods.prs) { scans.push(args); return structuredClone(prs); }
        if (method === methods.commits) return structuredClone(commits[args.pull_number] || []);
        if (method === methods.runs) return runs;
        if (method === methods.jobs) return options.jobs?.[args.run_id] || ['Validation', 'Backup and restore', 'Container build', 'Required checks', 'Publish GHCR image'].map((name) => ({ name, status: 'completed', conclusion: 'success' }));
        throw Error('Unexpected API pagination');
      },
    },
  };
}

test('requires complete explicit patch/minor metadata', () => {
  assert.equal(controller.hasOnlySafeUpdates(metadata('version-update:semver-minor', 'version-update:semver-patch')), true);
  for (const message of [metadata('version-update:semver-major'), metadata('unknown'), metadata('version-update:semver-patch', 'version-update:semver-major'), 'Bump a dependency', safe.replace('...\n', ''), safe.replace('updated-dependencies:', 'other-data:'), safe.replace('  update-type: version-update:semver-patch\n', '')]) assert.equal(controller.hasOnlySafeUpdates(message), false);
});

test('scans every open PR and merges at most one stable-order candidate, then dispatches main', async () => {
  const a = api({ prs: [pr(8, 'e'.repeat(40)), pr()] });
  await controller(a);
  assert.deepEqual(a.operations, [{ type: 'merge', owner: 'ralphschuler', repo: 'agent-workbench', pull_number: 7, sha: head, merge_method: 'squash' }]);
  assert.deepEqual(a.dispatches.map((d) => d.ref), ['main']);
  assert.equal(a.scans[0].state, 'open');
  assert.equal(a.scans[0].per_page, 100);
});

test('current main must pass the complete CI including publication', async () => {
  for (const mainRun of [run(100, 'main', main, { conclusion: 'failure' }), run(100, 'main', main, { status: 'in_progress', conclusion: null })]) {
    const a = api({ runs: [mainRun, run(200, pr().head.ref, head)] });
    await controller(a); assert.equal(a.operations.length, 0); assert.equal(a.dispatches.length, 0);
  }
  const jobs = ['Validation', 'Backup and restore', 'Container build', 'Required checks', 'Publish GHCR image'].map((name) => ({ name, status: 'completed', conclusion: name === 'Publish GHCR image' ? 'skipped' : 'success' }));
  const a = api({ jobs: { 100: jobs } }); await controller(a); assert.equal(a.operations.length, 0);
});

test('latest main run takes precedence over an older successful attempt/run', async () => {
  const a = api({ runs: [run(100, 'main', main), run(101, 'main', main, { status: 'queued', conclusion: null })] });
  await controller(a); assert.equal(a.operations.length, 0); assert.equal(a.dispatches.length, 0);
});

test('a rerun of an older run ID supersedes a newer-ID success by actual activity time', async () => {
  const earlier = { run_started_at: '2026-10-15T09:00:00Z', updated_at: '2026-10-15T09:30:00Z' };
  const rerun = { run_attempt: 2, run_started_at: '2026-10-15T08:00:00Z', updated_at: '2026-10-15T10:00:00Z', conclusion: 'failure' };
  const a = api({ runs: [run(100, 'main', main, rerun), run(101, 'main', main, earlier), run(200, pr().head.ref, head)] });
  await controller(a); assert.equal(a.operations.length, 0); assert.equal(a.dispatches.length, 0);
  const b = api({ runs: [run(100, 'main', main), run(200, pr().head.ref, head, rerun), run(201, pr().head.ref, head, earlier)] });
  await controller(b); assert.equal(b.operations.length, 0); assert.equal(b.dispatches.length, 0);
  assert.equal(controller.latestRun([run(200, 'main', main, { run_attempt: 1 }), run(200, 'main', main, { run_attempt: 2 })], repository, 'main', main, true).run_attempt, 2);
});

test('missing main CI recovers post-merge dispatch and performs no PR mutation', async () => {
  const a = api({ runs: [run(200, pr().head.ref, head)] });
  await controller(a); assert.equal(a.operations.length, 0); assert.deepEqual(a.dispatches.map((d) => d.ref), ['main']);
});

test('cancelled main CI and a missing post-merge run after the last PR recover', async () => {
  for (const options of [{ runs: [run(100, 'main', main, { conclusion: 'cancelled' })] }, { prs: [], runs: [] }]) {
    const a = api(options); await controller(a); assert.equal(a.operations.length, 0); assert.deepEqual(a.dispatches.map((d) => d.ref), ['main']);
  }
});

test('oldest queued or running current-head CI holds the queue, including stale-base candidates', async () => {
  for (const status of ['queued', 'in_progress', 'waiting', 'pending']) {
    const a = api({ prs: [pr(), pr(8, 'e'.repeat(40))], runs: [run(100, 'main', main), run(200, pr().head.ref, head, { status, conclusion: null })] });
    await controller(a); assert.equal(a.operations.length, 0); assert.equal(a.dispatches.length, 0); assert.equal(a.gets[8], undefined);
  }
});

test('missing and cancelled candidate CI dispatch only that branch, never a later candidate', async () => {
  for (const extra of [[], [run(200, pr().head.ref, head, { conclusion: 'cancelled' })]]) {
    const a = api({ prs: [pr(), pr(8, 'e'.repeat(40))], runs: [run(100, 'main', main), ...extra] });
    await controller(a); assert.equal(a.operations.length, 0); assert.deepEqual(a.dispatches.map((d) => d.ref), [pr().head.ref]); assert.equal(a.gets[8], undefined);
  }
});

test('definitively failed unchanged CI is not retried and does not block later eligible PRs', async () => {
  const p8 = pr(8, 'e'.repeat(40));
  const a = api({ prs: [pr(), p8], runs: [run(100, 'main', main), run(200, pr().head.ref, head, { conclusion: 'failure' }), run(201, p8.head.ref, p8.head.sha)] });
  await controller(a); assert.equal(a.operations[0].pull_number, 8); assert.deepEqual(a.dispatches.map((d) => d.ref), ['main']);
});

test('a confirmed merge conflict is skipped without mutation; unknown mergeability holds the queue', async () => {
  const conflicted = pr(); conflicted.mergeable = false;
  const a = api({ prs: [conflicted, pr(8, 'e'.repeat(40))] });
  await controller(a); assert.equal(a.operations.length, 1); assert.equal(a.operations[0].pull_number, 8);
  for (const value of [null, undefined]) {
    const pending = pr(); pending.mergeable = value;
    const b = api({ prs: [pending, pr(8, 'e'.repeat(40))] });
    await controller(b); assert.equal(b.operations.length, 0); assert.equal(b.dispatches.length, 0); assert.equal(b.gets[8], undefined);
  }
});

test('behind head is updated atomically, and no stale-head CI is dispatched before async completion', async () => {
  const a = api({ comparisons: { [`${main}...${head}`]: { status: 'diverged', behind_by: 1, merge_base_commit: { sha: old } } } });
  await controller(a); assert.deepEqual(a.operations, [{ type: 'update', owner: 'ralphschuler', repo: 'agent-workbench', pull_number: 7, expected_head_sha: head }]); assert.equal(a.dispatches.length, 0);
});

test('verified base-sync dispatches CI for the new branch head; later success can merge it', async () => {
  const opts = { prs: [pr(7, synced)], commits: { 7: [commit(head, old), syncCommit()] }, runs: [run(100, 'main', main)] };
  const a = api(opts); await controller(a); assert.equal(a.operations.length, 0); assert.deepEqual(a.dispatches.map((d) => d.ref), [pr().head.ref]);
  const b = api({ ...opts, runs: [run(100, 'main', main), run(201, pr().head.ref, synced, { event: 'workflow_dispatch' })] });
  await controller(b); assert.equal(b.operations[0].sha, synced);
});

test('immediately visible valid base update dispatches once and stops', async () => {
  const a = api({ comparisons: { [`${main}...${head}`]: { status: 'diverged', behind_by: 1, merge_base_commit: { sha: old } } },
    commits: { 7: [commit(head, old)] }, afterUpdate: (state) => { state.prs[0].head.sha = synced; state.commits[7].push(syncCommit()); } });
  await controller(a); assert.equal(a.operations.length, 1); assert.equal(a.operations[0].type, 'update'); assert.deepEqual(a.dispatches.map((d) => d.ref), [pr().head.ref]);
});

test('base-sync exceptions reject unsigned, foreign, nonlinear, octopus, or non-main merges', async () => {
  const variants = [
    (c) => { c.commit.verification.verified = false; }, (c) => { c.author.login = 'attacker'; }, (c) => { c.committer.login = 'attacker'; },
    (c) => { c.parents.push({ sha: old }); }, (c) => { c.parents[0].sha = old; }, (c) => { c.parents[1].sha = 'f'.repeat(40); },
    (c) => { c.author.login = bot; c.committer.login = bot; c.commit.message = safe; },
  ];
  for (const change of variants) {
    const bridge = syncCommit(); change(bridge);
    const a = api({ prs: [pr(7, synced)], commits: { 7: [commit(head, old), bridge] }, comparisons: { [`${'f'.repeat(40)}...${main}`]: { status: 'diverged', behind_by: 2, merge_base_commit: { sha: old } } } });
    await controller(a); assert.equal(a.operations.length, 0); assert.equal(a.dispatches.length, 0);
  }
});

test('normal commits retain signed bot metadata requirements, and every history entry must match', async () => {
  const variants = [(c) => { c.commit.verification.verified = false; }, (c) => { c.author.login = 'someone'; }, (c) => { c.commit.message = metadata('version-update:semver-major'); }, (c) => { c.sha = synced; }, (c) => { c.parents = []; }];
  for (const change of variants) { const c = commit(); change(c); const a = api({ commits: { 7: [c] } }); await controller(a); assert.equal(a.operations.length, 0); }
  const a = api({ prs: [pr(7, synced)], commits: { 7: [syncCommit()] } }); await controller(a); assert.equal(a.operations.length, 0);
});

test('foreign sources, drafts, target changes and maintainer changes remain excluded', async () => {
  const variants = [(p) => { p.head.repo.full_name = 'attacker/fork'; }, (p) => { p.base.ref = 'other'; }, (p) => { p.draft = true; }, (p) => { p.state = 'closed'; }, (p) => { p.user.login = 'someone'; }, (p) => { p.head.ref = 'other'; }, (p) => { p.body = 'Maintainer changes'; }];
  for (const change of variants) { const p = pr(); change(p); const a = api({ prs: [p] }); await controller(a); assert.equal(a.operations.length, 0); assert.equal(a.dispatches.length, 0); }
});

test('wrong workflow, stale SHA, foreign repository and wrong branch runs cannot establish tested head', async () => {
  for (const extra of [{ path: '.github/workflows/other.yml' }, { head_sha: old }, { head_repository: { full_name: 'attacker/fork' } }, { head_branch: 'other' }, { event: 'push' }]) {
    const a = api({ runs: [run(100, 'main', main), run(200, pr().head.ref, head, extra)] }); await controller(a); assert.equal(a.operations.length, 0); assert.deepEqual(a.dispatches.map((d) => d.ref), [pr().head.ref]);
  }
});

test('head and main are rechecked immediately before merge/update/dispatch', async () => {
  for (const extra of [
    { mainReads: [main, old] },
    { prRead: (p, count) => { if (count > 1) p.head.sha = synced; return p; } },
  ]) {
    const a = api(extra); await controller(a); assert.equal(a.operations.length, 0); assert.equal(a.dispatches.length, 0);
    const b = api({ ...extra, comparisons: { [`${main}...${head}`]: { status: 'behind', behind_by: 2, merge_base_commit: { sha: old } } } }); await controller(b); assert.equal(b.operations.length, 0);
  }
});

test('rerun races or missing required jobs cannot authorize a merge', async () => {
  const a = api({ currentRun: { run_attempt: 2 } }); await controller(a); assert.equal(a.operations.length, 0);
  const b = api({ jobs: { 200: [{ name: 'Required checks', status: 'completed', conclusion: 'success' }] } }); await controller(b); assert.equal(b.operations.length, 0);
});

test('unknown/neutral results or incomplete required jobs hold the oldest candidate', async () => {
  for (const conclusion of ['neutral', 'skipped', null]) {
    const a = api({ prs: [pr(), pr(8, 'e'.repeat(40))], runs: [run(100, 'main', main), run(200, pr().head.ref, head, { conclusion })] });
    await controller(a); assert.equal(a.operations.length, 0); assert.equal(a.dispatches.length, 0); assert.equal(a.gets[8], undefined);
  }
});

test('branch protection and SHA rejections stop the tick without a second mutation', async () => {
  for (const status of [403, 405, 409, 422]) { const a = api({ prs: [pr(), pr(8, 'e'.repeat(40))], mergeError: { status } }); await controller(a); assert.equal(a.operations.length, 1); assert.equal(a.dispatches.length, 0); }
  const a = api({ updateError: { status: 422 }, comparisons: { [`${main}...${head}`]: { status: 'diverged', behind_by: 1 } } }); await controller(a); assert.equal(a.operations.length, 1);
  await assert.rejects(controller(api({ mergeError: Object.assign(Error('server error'), { status: 500 }) })), /server error/);
});

test('post-merge dispatch failures stay visible for scheduled recovery', async () => {
  const a = api({ dispatchError: Error('dispatch denied') }); await assert.rejects(controller(a), /dispatch denied/); assert.equal(a.operations.length, 1); assert.equal(a.dispatches.length, 1);
});

test('controller workflow serializes the repository and receives main/failure/manual/recovery events', () => {
  const workflow = fs.readFileSync(new URL('../workflows/dependabot-merge.yml', `file://${__filename}`), 'utf8');
  assert.match(workflow, /group: dependabot-queue\n\s+cancel-in-progress: false/);
  assert.match(workflow, /workflow_dispatch:/); assert.match(workflow, /schedule:/); assert.match(workflow, /workflow_run:/);
  assert.doesNotMatch(workflow, /workflow_run\.conclusion|workflow_run\.event|workflow_run\.head_branch/);
  assert.match(workflow, /persist-credentials: false/);
});
