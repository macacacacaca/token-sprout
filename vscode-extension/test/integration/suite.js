// @ts-check
'use strict';

// Runs inside the VS Code extension host (see run.js).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');

const SPROUT = 200_000; // food tokens per sprout unit

/** Write plant state the way the proxy does: temp file + atomic rename. */
function writeState(/** @type {Record<string, unknown>} */ changes) {
  const home = /** @type {string} */ (process.env.TOKEN_SPROUT_HOME);
  const state = {
    version: '0.1.0',
    generation: 1,
    total_tokens: 1234567,
    total_input_tokens: 200000,
    total_output_tokens: 300000,
    total_cache_creation_tokens: 100000,
    total_cache_read_tokens: 634567,
    current_exp: SPROUT * 2 + 129_000,
    level: 2,
    stage: 'sprout',
    active_requests: 0,
    live_tokens_estimate: 0,
    last_request_tokens: 1284,
    last_input_tokens: 220,
    last_output_tokens: 464,
    last_request_started_at: null,
    last_request_finished_at: '2026-10-15T08:41:19+00:00',
    ...changes,
  };
  const target = path.join(home, 'plant_state.json');
  const temp = `${target}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(state), { mode: 0o600 });
  fs.renameSync(temp, target);
}

/**
 * @param {() => boolean} predicate
 * @param {() => string} describe
 */
async function waitFor(predicate, describe, timeout = 20_000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() > deadline) assert.fail(`timed out: ${describe()}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

async function run() {
  writeState({});
  const extension = vscode.extensions.getExtension('macacacacaca.token-sprout');
  assert.ok(extension, 'extension is installed');
  const api = await extension.activate();
  const seen = () => `feed=${api.feedStatus()} text=${JSON.stringify(api.statusBarText())}`;

  await waitFor(() => api.statusBarText() === '🌱 2/20', seen);
  console.log('ok idle status bar:', api.statusBarText());

  writeState({ active_requests: 2, live_tokens_estimate: 5000 });
  await waitFor(() => api.statusBarText() === '🌱 2/20 · 💧 67% · +5,000', seen);
  console.log('ok thinking status bar:', api.statusBarText());

  await vscode.commands.executeCommand('tokenSprout.showPlant');
  await waitFor(() => api.viewResolved(), () => 'plant view never resolved');
  console.log('ok sidebar view resolved');

  writeState({ current_exp: 4_000_000, level: 3, stage: 'leaf' });
  await waitFor(() => api.statusBarText() === '✨ Grew into Leaf!', seen);
  console.log('ok stage-up notice:', api.statusBarText());
  await waitFor(() => api.statusBarText() === '🪴 1/20', seen, 10_000);
  console.log('ok notice clears:', api.statusBarText());

  await vscode.workspace
    .getConfiguration('tokenSprout')
    .update('executablePath', '/nonexistent/token-sprout', vscode.ConfigurationTarget.Global);
  await waitFor(() => api.feedStatus() === 'missing' && api.statusBarText() === '$(warning) Token Sprout', seen);
  console.log('ok missing CLI warning');
}

module.exports = { run };
