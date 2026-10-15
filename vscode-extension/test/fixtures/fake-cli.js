// @ts-check
'use strict';

// Stands in for `token-sprout watch --json` in the feed tests.
// FAKE_MODE picks the behavior; argv after the script mirrors the real CLI.
const fs = require('node:fs');
const path = require('node:path');

const snapshots = JSON.parse(fs.readFileSync(path.join(__dirname, 'snapshots.json'), 'utf8'));
const mode = process.env.FAKE_MODE ?? 'stream';
const args = process.argv.slice(2);

/** @param {unknown} value */
const emit = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);

if (args.join(' ') !== 'watch --json') {
  process.stderr.write(`unexpected arguments: ${args.join(' ')}\n`);
  process.exit(64);
}

switch (mode) {
  case 'stream':
    emit(snapshots.idle_sprout);
    setTimeout(() => emit(snapshots.thinking_sprout), 30);
    setInterval(() => {}, 1000); // stay alive like the real feed
    break;
  case 'garbage-then-good':
    process.stdout.write('this is not json\n');
    emit(snapshots.fresh_seed);
    setInterval(() => {}, 1000);
    break;
  case 'outdated':
    process.stderr.write('usage: token-sprout [-h]\ntoken-sprout: error: unrecognized arguments: --json\n');
    process.exit(2);
    break;
  case 'future-protocol':
    emit({ ...snapshots.idle_sprout, protocol: 99 });
    setInterval(() => {}, 1000);
    break;
  case 'crash':
    process.stderr.write('Traceback (most recent call last): boom\n');
    process.exit(1);
    break;
  default:
    process.exit(70);
}
