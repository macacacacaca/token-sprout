// @ts-check
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const format = require('../src/format');

/** Shared with tests/test_feed.py: real `watch --json` payloads. */
const SNAPSHOTS = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'snapshots.json'), 'utf8'));

/** @type {import('../src/format').Translate} */
const t = (message, ...args) => message.replace(/\{(\d+)\}/g, (_, index) => String(args[Number(index)]));

/** @param {string} name */
function snapshot(name) {
  const parsed = format.parseSnapshot(JSON.stringify(SNAPSHOTS[name]));
  assert.ok(parsed.ok, `${name} should parse`);
  return parsed.snapshot;
}

test('every contract snapshot parses', () => {
  for (const name of Object.keys(SNAPSHOTS)) snapshot(name);
});

test('status bar text mirrors the Claude Code status line', () => {
  assert.equal(format.statusBarText(snapshot('fresh_seed'), t), '🌰 0/20');
  assert.equal(format.statusBarText(snapshot('idle_sprout'), t), '🌱 2/20');
  assert.equal(format.statusBarText(snapshot('thinking_sprout'), t), '🌱 2/20 · 💧 67% · +5,000');
  assert.equal(format.statusBarText(snapshot('bloom'), t), '🌸 in bloom');
});

test('the pile option repeats the stage glyph once per unit', () => {
  assert.equal(format.statusBarText(snapshot('idle_sprout'), t, { showPile: true }), '🌱🌱 2/20');
  assert.equal(format.statusBarText(snapshot('fresh_seed'), t, { showPile: true }), '🌰 0/20');
});

test('tooltip carries the CLI numbers, formatted', () => {
  const text = format.tooltipMarkdown(snapshot('thinking_sprout'), t);
  assert.match(text, /Generation 1/);
  assert.match(text, /Sprout · 2\/20/);
  assert.match(text, /66,000 tokens to the next Sprout \(67%\)/);
  assert.match(text, /Thinking — 2 requests in flight/);
  assert.match(text, /\+5,000 tokens estimated/);
  assert.match(text, /Lifetime: 1,234,567 tokens/);
  assert.match(text, /cache read 634,567/);
});

test('bloom tooltip announces the next generation', () => {
  const text = format.tooltipMarkdown(snapshot('bloom'), t);
  assert.match(text, /In bloom/);
  assert.match(text, /generation 4/);
});

test('transitions are localized from the structured event', () => {
  /** @type {import('../src/format').Translate} */
  const zh = (message, ...args) =>
    ({ Sprout: '幼芽', '✨ Grew into {0}!': '✨ 長成{0}了！' })[message]?.replace(/\{(\d+)\}/g, (_, i) => String(args[Number(i)])) ??
    t(message, ...args);
  assert.equal(format.transitionText(snapshot('stage_up'), t), '✨ Grew into Sprout!');
  assert.equal(format.transitionText(snapshot('stage_up'), zh), '✨ 長成幼芽了！');
  assert.equal(format.transitionText(snapshot('idle_sprout'), t), null);
});

test('rejects malformed or foreign lines', () => {
  const good = SNAPSHOTS.idle_sprout;
  /** @param {unknown} value */
  const parse = (value) => format.parseSnapshot(typeof value === 'string' ? value : JSON.stringify(value));
  assert.equal(parse('not json').ok, false);
  assert.equal(parse('[]').ok, false);
  assert.deepEqual(parse({ ...good, protocol: 2 }), { ok: false, reason: 'protocol', protocol: 2 });
  assert.equal(parse({ ...good, view: { ...good.view, stage: 'tree' } }).ok, false);
  assert.equal(parse({ ...good, view: { ...good.view, count: -1 } }).ok, false);
  assert.equal(parse({ ...good, view: { ...good.view, unit_progress_percent: 101 } }).ok, false);
  assert.equal(parse({ ...good, totals: { ...good.totals, total: '1' } }).ok, false);
  assert.equal(parse({ ...good, glyph: 'x'.repeat(17) }).ok, false);
  assert.equal(parse({ ...good, transition: { kind: 'stage_up', stage: '<b>' } }).ok, false);
  assert.equal(parse('x'.repeat(70 * 1024)).ok, false);
});

test('older payloads without transition fields still parse', () => {
  const { transition, notice, ...legacy } = SNAPSHOTS.idle_sprout;
  const parsed = format.parseSnapshot(JSON.stringify(legacy));
  assert.ok(parsed.ok);
  assert.equal(parsed.snapshot.transition, null);
  assert.equal(parsed.snapshot.notice, null);
});

test('markdown from outside is escaped', () => {
  assert.equal(format.escapeMarkdown('[x](y)*'), '\\[x\\]\\(y\\)\\*');
});

test('view model: running, starting, and problem states', () => {
  const running = format.viewModel({ kind: 'running' }, snapshot('thinking_sprout'), null, t);
  assert.equal(running.state, 'running');
  assert.equal(running.plant?.count, 2);
  assert.equal(running.plant?.percent, 67);
  assert.equal(running.plant?.thinking, true);
  assert.equal(running.plant?.totals.cacheRead, '634,567');
  assert.equal(running.plant?.last, 'Last reply: 1,284 tokens');

  const starting = format.viewModel({ kind: 'starting' }, null, null, t);
  assert.equal(starting.state, 'starting');
  assert.equal(starting.plant, null);

  const missing = format.viewModel({ kind: 'missing' }, null, null, t);
  assert.equal(missing.state, 'problem');
  assert.match(missing.problem?.title ?? '', /not found/);

  const outdated = format.problemText({ kind: 'outdated' }, t);
  assert.match(outdated?.detail ?? '', /0\.2\.0/);
  assert.equal(format.problemText({ kind: 'running' }, t), null);
});
