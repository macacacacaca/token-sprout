// @ts-check
'use strict';

const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const { PlantFeed, FEED_ARGS } = require('../src/feed');

const FAKE_CLI = path.join(__dirname, 'fixtures', 'fake-cli.js');

/**
 * A feed whose "executable" is the fake CLI run by this Node binary.
 * @param {string} mode
 * @param {{resolve?: () => Promise<string | null>}} [options]
 */
function fakeFeed(mode, options = {}) {
  /** @type {import('node:child_process').ChildProcess[]} */
  const children = [];
  const feed = new PlantFeed({
    resolveExecutable: options.resolve ?? (async () => '/fake/token-sprout'),
    env: { ...process.env, FAKE_MODE: mode },
    spawn: (command, args, spawnOptions) => {
      assert.equal(command, '/fake/token-sprout');
      assert.deepEqual(args, FEED_ARGS);
      const child = childProcess.spawn(process.execPath, [FAKE_CLI, ...args], spawnOptions);
      children.push(child);
      return child;
    },
  });
  /** @type {string[]} */
  const statuses = [];
  /** @type {any[]} */
  const snapshots = [];
  feed.on('status', (status) => statuses.push(status.kind));
  feed.on('snapshot', (snapshot) => snapshots.push(snapshot));
  return { feed, statuses, snapshots, children };
}

/**
 * @param {() => boolean} predicate
 * @param {number} [timeout]
 */
async function until(predicate, timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('timed out waiting for condition');
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

test('streams validated snapshots and reports running', async () => {
  const { feed, statuses, snapshots } = fakeFeed('stream');
  feed.start();
  try {
    await until(() => snapshots.length >= 2);
    assert.equal(snapshots[0].view.stage, 'sprout');
    assert.equal(snapshots[1].thinking, true);
    assert.deepEqual(statuses, ['starting', 'running']);
  } finally {
    feed.stop();
  }
});

test('skips unparsable lines without stopping', async () => {
  const { feed, snapshots, statuses } = fakeFeed('garbage-then-good');
  feed.start();
  try {
    await until(() => snapshots.length === 1);
    assert.equal(snapshots[0].view.stage, 'seed');
    assert.ok(statuses.includes('running'));
  } finally {
    feed.stop();
  }
});

test('an older CLI without watch --json is reported as outdated', async () => {
  const { feed, statuses } = fakeFeed('outdated');
  feed.start();
  try {
    await until(() => statuses.includes('outdated'));
  } finally {
    feed.stop();
  }
});

test('a different protocol is reported as incompatible', async () => {
  const { feed, statuses, snapshots } = fakeFeed('future-protocol');
  feed.start();
  try {
    await until(() => statuses.includes('incompatible'));
    assert.equal(snapshots.length, 0);
  } finally {
    feed.stop();
  }
});

test('a crash is an error and retries with backoff', async () => {
  const { feed, statuses, children } = fakeFeed('crash');
  feed.start();
  try {
    await until(() => statuses.includes('error'));
    await until(() => children.length >= 2, 4000); // first retry after ~1 s
    assert.ok(feed.backoff > 1000, 'backoff grows after each failure');
  } finally {
    feed.stop();
  }
});

test('a missing executable is reported, and nothing is spawned', async () => {
  const { feed, statuses, children } = fakeFeed('stream', { resolve: async () => null });
  feed.start();
  try {
    await until(() => statuses.includes('missing'));
    assert.equal(children.length, 0);
  } finally {
    feed.stop();
  }
});

test('stop() terminates the CLI and silences later events', async () => {
  const { feed, statuses, snapshots, children } = fakeFeed('stream');
  feed.start();
  await until(() => snapshots.length >= 1);
  feed.stop();
  const child = children[0];
  await until(() => child.exitCode !== null || child.signalCode !== null);
  const seen = statuses.length;
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.equal(statuses.length, seen, 'no status after stop');
});
