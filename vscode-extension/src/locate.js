// @ts-check
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

/**
 * Find the `token-sprout` executable.
 *
 * Order: the machine-scoped setting, then absolute `PATH` entries, then
 * pipx's default `~/.local/bin`. Relative `PATH` entries are skipped so a
 * workspace folder can never supply the program that runs.
 *
 * @param {{
 *   configured?: string,
 *   env?: NodeJS.ProcessEnv,
 *   platform?: NodeJS.Platform,
 *   homedir?: string,
 *   isExecutable?: (file: string) => Promise<boolean>,
 * }} [options]
 * @returns {Promise<string | null>}
 */
async function resolveExecutable(options = {}) {
  const env = options.env ?? process.env;
  const platform = options.platform ?? process.platform;
  const home = options.homedir ?? os.homedir();
  const check = options.isExecutable ?? isExecutable;
  const name = platform === 'win32' ? 'token-sprout.exe' : 'token-sprout';

  const configured = (options.configured ?? '').trim();
  if (configured) {
    const expanded = configured === '~' || configured.startsWith('~/')
      ? path.join(home, configured.slice(1))
      : configured;
    // An explicit setting is authoritative: no silent fallback elsewhere.
    return path.isAbsolute(expanded) && (await check(expanded)) ? expanded : null;
  }

  const candidates = (env.PATH ?? '')
    .split(path.delimiter)
    .filter((dir) => dir && path.isAbsolute(dir))
    .map((dir) => path.join(dir, name));
  candidates.push(path.join(home, '.local', 'bin', name));

  for (const candidate of new Set(candidates)) {
    if (await check(candidate)) return candidate;
  }
  return null;
}

/** @param {string} file */
async function isExecutable(file) {
  try {
    const stat = await fs.promises.stat(file);
    if (!stat.isFile()) return false;
    await fs.promises.access(file, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

module.exports = { resolveExecutable, isExecutable };
