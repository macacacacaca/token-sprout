// @ts-check
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { resolveExecutable, isExecutable } = require('../src/locate');

/** @param {string[]} executables */
const only = (executables) => async (/** @type {string} */ file) => executables.includes(file);

test('a configured path wins and never falls back', async () => {
  const found = await resolveExecutable({
    configured: '/opt/sprout/bin/token-sprout',
    env: { PATH: '/usr/bin' },
    homedir: '/home/me',
    isExecutable: only(['/opt/sprout/bin/token-sprout', '/usr/bin/token-sprout']),
  });
  assert.equal(found, '/opt/sprout/bin/token-sprout');

  const broken = await resolveExecutable({
    configured: '/missing/token-sprout',
    env: { PATH: '/usr/bin' },
    homedir: '/home/me',
    isExecutable: only(['/usr/bin/token-sprout']),
  });
  assert.equal(broken, null);
});

test('a configured path may start with ~/', async () => {
  const found = await resolveExecutable({
    configured: '~/.local/bin/token-sprout',
    homedir: '/home/me',
    isExecutable: only(['/home/me/.local/bin/token-sprout']),
  });
  assert.equal(found, '/home/me/.local/bin/token-sprout');
});

test('relative configured paths are refused', async () => {
  const found = await resolveExecutable({
    configured: './token-sprout',
    homedir: '/home/me',
    isExecutable: async () => true,
  });
  assert.equal(found, null);
});

test('PATH search skips relative entries, then tries ~/.local/bin', async () => {
  /** @type {string[]} */
  const checked = [];
  const found = await resolveExecutable({
    env: { PATH: ['.', 'node_modules/.bin', '/usr/local/bin'].join(path.delimiter) },
    homedir: '/home/me',
    isExecutable: async (file) => {
      checked.push(file);
      return file === '/home/me/.local/bin/token-sprout';
    },
  });
  assert.equal(found, '/home/me/.local/bin/token-sprout');
  assert.deepEqual(checked, ['/usr/local/bin/token-sprout', '/home/me/.local/bin/token-sprout']);
});

test('nothing found means null', async () => {
  const found = await resolveExecutable({ env: { PATH: '' }, homedir: '/home/me', isExecutable: async () => false });
  assert.equal(found, null);
});

test('isExecutable requires an executable regular file', { skip: process.platform === 'win32' }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ts-locate-'));
  const file = path.join(dir, 'token-sprout');
  fs.writeFileSync(file, '#!/bin/sh\n');
  fs.chmodSync(file, 0o644);
  assert.equal(await isExecutable(file), false);
  fs.chmodSync(file, 0o755);
  assert.equal(await isExecutable(file), true);
  assert.equal(await isExecutable(dir), false);
  fs.rmSync(dir, { recursive: true });
});
