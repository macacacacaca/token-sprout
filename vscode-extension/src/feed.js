// @ts-check
'use strict';

const childProcess = require('node:child_process');
const { EventEmitter } = require('node:events');
const readline = require('node:readline');
const { parseSnapshot } = require('./format');

const FEED_ARGS = ['watch', '--json'];
// argparse's wording when an older CLI does not know `watch --json`.
const OUTDATED_STDERR = /unrecognized arguments|invalid choice/i;
const STDERR_LIMIT = 4096;
const BACKOFF_START_MS = 1_000;
const BACKOFF_MAX_MS = 60_000;
const HEALTHY_RUN_MS = 30_000;
const SETUP_RETRY_MS = 60_000;

/**
 * @typedef {import('./format').FeedStatus} FeedStatus
 * @typedef {import('./format').Snapshot} Snapshot
 * @typedef {{
 *   resolveExecutable: () => Promise<string | null>,
 *   spawn?: (command: string, args: string[], options: childProcess.SpawnOptions) => childProcess.ChildProcess,
 *   env?: NodeJS.ProcessEnv,
 *   log?: (message: string) => void,
 *   now?: () => number,
 * }} FeedOptions
 */

/**
 * Runs `token-sprout watch --json` and turns its lines into events:
 * `snapshot` (a validated Snapshot) and `status` (a FeedStatus).
 *
 * The CLI owns every number; this class only supervises the process. It
 * never reads or writes ~/.token-sprout/ itself.
 */
class PlantFeed extends EventEmitter {
  /** @param {FeedOptions} options */
  constructor(options) {
    super();
    this.resolveExecutable = options.resolveExecutable;
    this.spawn = options.spawn ?? childProcess.spawn;
    this.env = options.env ?? process.env;
    this.log = options.log ?? (() => {});
    this.now = options.now ?? Date.now;
    /** @type {FeedStatus} */
    this.status = { kind: 'starting' };
    /** @type {childProcess.ChildProcess | null} */
    this.child = null;
    /** @type {ReturnType<typeof setTimeout> | null} */
    this.timer = null;
    this.running = false;
    this.launchId = 0;
    this.backoff = BACKOFF_START_MS;
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.backoff = BACKOFF_START_MS;
    void this.launch();
  }

  stop() {
    this.running = false;
    this.launchId += 1; // orphan every callback of the current launch
    this.clearTimer();
    const child = this.child;
    this.child = null;
    if (child && child.exitCode === null && child.signalCode === null) child.kill();
  }

  restart() {
    this.stop();
    this.start();
  }

  clearTimer() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  /** @param {number} delay */
  schedule(delay) {
    this.clearTimer();
    if (!this.running) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.launch();
    }, delay);
  }

  scheduleBackoff() {
    this.schedule(this.backoff);
    this.backoff = Math.min(this.backoff * 2, BACKOFF_MAX_MS);
  }

  /** @param {FeedStatus} status */
  setStatus(status) {
    this.status = status;
    this.emit('status', status);
  }

  async launch() {
    if (!this.running) return;
    const id = ++this.launchId;
    const current = () => id === this.launchId && this.running;
    if (this.status.kind !== 'running') this.setStatus({ kind: 'starting' });

    /** @type {string | null} */
    let executable = null;
    try {
      executable = await this.resolveExecutable();
    } catch {
      executable = null;
    }
    if (!current()) return;
    if (!executable) {
      this.log('token-sprout CLI not found');
      this.setStatus({ kind: 'missing' });
      this.schedule(SETUP_RETRY_MS);
      return;
    }

    /** @type {childProcess.ChildProcess} */
    let child;
    try {
      child = this.spawn(executable, FEED_ARGS, {
        stdio: ['pipe', 'pipe', 'pipe'],
        env: this.env,
        windowsHide: true,
      });
    } catch (error) {
      this.fail({ kind: 'error', detail: describeError(error) });
      return;
    }
    this.child = child;
    this.log(`started ${executable} ${FEED_ARGS.join(' ')}`);

    const startedAt = this.now();
    let sawSnapshot = false;
    let stderr = '';
    let settled = false;
    /** @type {FeedStatus | null} */
    let verdict = null;

    /** @param {() => void} handle */
    const settle = (handle) => {
      if (settled || !current()) return;
      settled = true;
      if (this.child === child) this.child = null;
      handle();
    };

    child.stderr?.setEncoding('utf8');
    child.stderr?.on('data', (chunk) => {
      stderr = (stderr + chunk).slice(-STDERR_LIMIT);
    });

    if (child.stdout) {
      const lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
      lines.on('line', (line) => {
        if (!current() || settled) return;
        const parsed = parseSnapshot(line);
        if (!parsed.ok) {
          if (parsed.reason === 'protocol') {
            verdict = { kind: 'incompatible', detail: `protocol ${String(parsed.protocol)}` };
            child.kill();
          } else {
            this.log(`ignored a feed line (${parsed.reason})`);
          }
          return;
        }
        sawSnapshot = true;
        if (this.status.kind !== 'running') this.setStatus({ kind: 'running' });
        this.emit('snapshot', parsed.snapshot);
      });
      child.once('close', () => lines.close());
    }

    child.once('error', (error) => {
      settle(() => {
        const code = /** @type {NodeJS.ErrnoException} */ (error).code;
        if (code === 'ENOENT') {
          this.setStatus({ kind: 'missing' });
          this.schedule(SETUP_RETRY_MS);
        } else {
          this.fail({ kind: 'error', detail: describeError(error) });
        }
      });
    });

    child.once('close', (code, signal) => {
      settle(() => {
        if (verdict) {
          this.setStatus(verdict);
          this.schedule(SETUP_RETRY_MS);
          return;
        }
        if (!sawSnapshot && code === 2 && OUTDATED_STDERR.test(stderr)) {
          this.log('token-sprout CLI does not support watch --json');
          this.setStatus({ kind: 'outdated' });
          this.schedule(SETUP_RETRY_MS);
          return;
        }
        if (this.now() - startedAt >= HEALTHY_RUN_MS) this.backoff = BACKOFF_START_MS;
        const detail = signal ? `The CLI stopped (${signal})` : `The CLI exited with code ${code}`;
        if (stderr.trim()) this.log(`feed stderr: ${stderr.trim().split('\n').slice(-3).join(' | ')}`);
        this.fail({ kind: 'error', detail });
      });
    });
  }

  /** @param {FeedStatus} status */
  fail(status) {
    this.log(status.detail ?? status.kind);
    this.setStatus(status);
    this.scheduleBackoff();
  }
}

/** @param {unknown} error */
function describeError(error) {
  return error instanceof Error ? error.message : String(error);
}

module.exports = { PlantFeed, FEED_ARGS };
