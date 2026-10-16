// @ts-check
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const claudeCode = require('../src/claudeCode');
const format = require('../src/format');

/** @type {import('../src/format').Translate} */
const t = (message, ...args) => message.replace(/\{(\d+)\}/g, (_, index) => String(args[Number(index)]));

test('recognizes only Token Sprout as the wrapper', () => {
  assert.equal(claudeCode.isTokenSproutWrapper('/home/me/.local/bin/token-sprout-claude'), true);
  assert.equal(claudeCode.isTokenSproutWrapper('C:\\tools\\token-sprout-claude.exe'.replace(/\\/g, '/')), true);
  assert.equal(claudeCode.isTokenSproutWrapper('/usr/local/bin/other-wrapper'), false);
  assert.equal(claudeCode.isTokenSproutWrapper('/usr/local/bin/token-sprout'), false);
  assert.equal(claudeCode.isTokenSproutWrapper(''), false);
  assert.equal(claudeCode.isTokenSproutWrapper(undefined), false);
});

test('connection state', () => {
  assert.equal(claudeCode.connectionState({ installed: false, wrapper: undefined }), 'unavailable');
  assert.equal(claudeCode.connectionState({ installed: true, wrapper: undefined }), 'disconnected');
  assert.equal(claudeCode.connectionState({ installed: true, wrapper: '  ' }), 'disconnected');
  assert.equal(claudeCode.connectionState({ installed: true, wrapper: '/x/token-sprout-claude' }), 'connected');
  assert.equal(claudeCode.connectionState({ installed: true, wrapper: '/x/corp-wrapper' }), 'foreign');
});

test('the wrapper is found next to the CLI, then next to its symlink target', async () => {
  const sibling = await claudeCode.resolveWrapper({
    cli: '/home/me/.local/bin/token-sprout',
    env: { PATH: '' },
    homedir: '/home/me',
    isExecutable: async (file) => file === '/home/me/.local/bin/token-sprout-claude',
    realpath: async () => '/repo/.venv/bin/token-sprout',
  });
  assert.equal(sibling, '/home/me/.local/bin/token-sprout-claude');

  const viaSymlink = await claudeCode.resolveWrapper({
    cli: '/home/me/.local/bin/token-sprout',
    env: { PATH: '' },
    homedir: '/home/me',
    isExecutable: async (file) => file === '/repo/.venv/bin/token-sprout-claude',
    realpath: async () => '/repo/.venv/bin/token-sprout',
  });
  assert.equal(viaSymlink, '/repo/.venv/bin/token-sprout-claude');
});

test('without a CLI path, absolute PATH entries and ~/.local/bin are searched', async () => {
  /** @type {string[]} */
  const checked = [];
  const found = await claudeCode.resolveWrapper({
    cli: null,
    env: { PATH: ['relative/bin', '/opt/tools'].join(path.delimiter) },
    homedir: '/home/me',
    isExecutable: async (file) => {
      checked.push(file);
      return false;
    },
  });
  assert.equal(found, null);
  assert.deepEqual(checked, ['/opt/tools/token-sprout-claude', '/home/me/.local/bin/token-sprout-claude']);
});

test('a dangling CLI symlink does not break the search', async () => {
  const found = await claudeCode.resolveWrapper({
    cli: '/home/me/.local/bin/token-sprout',
    env: { PATH: '' },
    homedir: '/home/me',
    isExecutable: async () => false,
    realpath: async () => {
      throw new Error('ENOENT');
    },
  });
  assert.equal(found, null);
});

test('connection card text and tooltip hint', () => {
  assert.equal(format.connectionText('unavailable', t), null);
  assert.equal(format.connectionText('connected', t)?.action, '');
  const needed = format.connectionText('disconnected', t);
  assert.equal(needed?.action, 'Connect');
  assert.match(needed?.detail ?? '', /don't feed the plant/);
  assert.match(format.connectionText('foreign', t)?.detail ?? '', /another process wrapper/);

  const snapshots = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'snapshots.json'), 'utf8'));
  const parsed = format.parseSnapshot(JSON.stringify(snapshots.idle_sprout));
  assert.ok(parsed.ok);
  assert.match(format.tooltipMarkdown(parsed.snapshot, t, 'disconnected'), /Connect Claude Code/);
  assert.doesNotMatch(format.tooltipMarkdown(parsed.snapshot, t, 'connected'), /Connect Claude Code/);

  const model = format.viewModel({ kind: 'running' }, parsed.snapshot, null, t, 'disconnected');
  assert.equal(model.connection?.state, 'disconnected');
});
