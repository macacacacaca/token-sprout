// @ts-check
'use strict';

/**
 * Connecting Anthropic's Claude Code extension to Token Sprout.
 *
 * That extension starts its own `claude` process, so the shell integration
 * never sees it. Its `claudeCode.claudeProcessWrapper` setting runs a
 * wrapper as `<wrapper> <claude> <args...>`; pointing it at
 * `token-sprout-claude` routes those sessions through the local proxy.
 * No `vscode` import: unit tests run in plain Node.
 */

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { isExecutable } = require('./locate');

const CLAUDE_CODE_EXTENSION = 'anthropic.claude-code';
const WRAPPER_SECTION = 'claudeCode';
const WRAPPER_KEY = 'claudeProcessWrapper';
const WRAPPER_NAME = 'token-sprout-claude';

/**
 * @typedef {'unavailable' | 'disconnected' | 'connected' | 'foreign'} ConnectionState
 */

/** @param {unknown} value */
function isTokenSproutWrapper(value) {
  if (typeof value !== 'string' || !value.trim()) return false;
  return path.basename(value.trim()).replace(/\.exe$/i, '') === WRAPPER_NAME;
}

/**
 * @param {{installed: boolean, wrapper: unknown}} input
 * @returns {ConnectionState}
 */
function connectionState({ installed, wrapper }) {
  if (!installed) return 'unavailable';
  if (isTokenSproutWrapper(wrapper)) return 'connected';
  if (typeof wrapper === 'string' && wrapper.trim()) return 'foreign';
  return 'disconnected';
}

/**
 * Find `token-sprout-claude`: next to the CLI (as given, then with symlinks
 * resolved), then absolute `PATH` entries, then `~/.local/bin`.
 *
 * @param {{
 *   cli: string | null,
 *   env?: NodeJS.ProcessEnv,
 *   platform?: NodeJS.Platform,
 *   homedir?: string,
 *   isExecutable?: (file: string) => Promise<boolean>,
 *   realpath?: (file: string) => Promise<string>,
 * }} options
 * @returns {Promise<string | null>}
 */
async function resolveWrapper(options) {
  const env = options.env ?? process.env;
  const home = options.homedir ?? os.homedir();
  const check = options.isExecutable ?? isExecutable;
  const realpath = options.realpath ?? ((file) => fs.promises.realpath(file));
  const name = (options.platform ?? process.platform) === 'win32' ? `${WRAPPER_NAME}.exe` : WRAPPER_NAME;

  /** @type {string[]} */
  const candidates = [];
  if (options.cli) {
    candidates.push(path.join(path.dirname(options.cli), name));
    try {
      candidates.push(path.join(path.dirname(await realpath(options.cli)), name));
    } catch {
      // A dangling symlink just contributes no candidate.
    }
  }
  for (const dir of (env.PATH ?? '').split(path.delimiter)) {
    if (dir && path.isAbsolute(dir)) candidates.push(path.join(dir, name));
  }
  candidates.push(path.join(home, '.local', 'bin', name));

  for (const candidate of new Set(candidates)) {
    if (await check(candidate)) return candidate;
  }
  return null;
}

module.exports = {
  CLAUDE_CODE_EXTENSION,
  WRAPPER_SECTION,
  WRAPPER_KEY,
  WRAPPER_NAME,
  isTokenSproutWrapper,
  connectionState,
  resolveWrapper,
};
