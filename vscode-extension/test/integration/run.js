// @ts-check
'use strict';

/**
 * End-to-end check inside a real VS Code: the extension spawns the real
 * `token-sprout watch --json` against a throwaway TOKEN_SPROUT_HOME.
 *
 *   VSCODE_BIN=/path/to/code TOKEN_SPROUT_CLI=/path/to/token-sprout npm run test:integration
 *
 * Defaults: macOS VS Code from /Applications, and the repository's
 * .venv/bin/token-sprout. Everything runs in an isolated profile under the
 * OS temp dir (fresh user data and extensions directories).
 */
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const extensionRoot = path.resolve(__dirname, '..', '..');
const repoRoot = path.resolve(extensionRoot, '..');
const vscodeBin = process.env.VSCODE_BIN ?? '/Applications/Visual Studio Code.app/Contents/MacOS/Code';
const cli = process.env.TOKEN_SPROUT_CLI ?? path.join(repoRoot, '.venv', 'bin', 'token-sprout');

for (const [label, file] of [['VS Code', vscodeBin], ['token-sprout CLI', cli]]) {
  if (!fs.existsSync(file)) {
    console.error(`${label} not found at ${file}`);
    process.exit(2);
  }
}

const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'token-sprout-vscode-'));
const userData = path.join(sandbox, 'user-data');
const sproutHome = path.join(sandbox, 'sprout-home');
const workspace = path.join(sandbox, 'workspace');
for (const dir of [path.join(userData, 'User'), sproutHome, workspace]) fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(
  path.join(userData, 'User', 'settings.json'),
  JSON.stringify({
    'tokenSprout.executablePath': cli,
    'workbench.startupEditor': 'none',
    'window.restoreWindows': 'none',
    'telemetry.telemetryLevel': 'off',
    'update.mode': 'none',
  }),
);

// A clean environment: drop the host editor's variables so the child is a
// real, independent VS Code instead of an attached process.
/** @type {NodeJS.ProcessEnv} */
const env = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !/^(VSCODE_|ELECTRON_)/.test(key)),
);
env.TOKEN_SPROUT_HOME = sproutHome;

const child = spawn(
  vscodeBin,
  [
    workspace,
    `--extensionDevelopmentPath=${extensionRoot}`,
    `--extensionTestsPath=${path.join(__dirname, 'suite.js')}`,
    `--user-data-dir=${userData}`,
    `--extensions-dir=${path.join(sandbox, 'extensions')}`,
    '--disable-extensions',
    '--disable-workspace-trust',
    '--skip-welcome',
    '--skip-release-notes',
    '--disable-telemetry',
    '--new-window',
  ],
  { env, stdio: 'inherit' },
);

child.on('exit', (code, signal) => {
  fs.rmSync(sandbox, { recursive: true, force: true });
  if (signal) {
    console.error(`VS Code stopped by ${signal}`);
    process.exit(1);
  }
  console.log(code === 0 ? 'integration: passed' : `integration: failed (${code})`);
  process.exit(code ?? 1);
});
