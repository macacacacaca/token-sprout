// @ts-check
'use strict';

/**
 * Turns `token-sprout watch --json` snapshots into display text.
 *
 * Every growth number comes from the CLI's `game.plant_view()`; this module
 * only validates and formats it, so the editor can never drift from the
 * terminal views. No `vscode` import: unit tests run in plain Node.
 */

const PROTOCOL = 1;
const STAGES = ['seed', 'sprout', 'leaf', 'bud', 'bloom'];
const STAGE_LABELS = {
  seed: 'Seed',
  sprout: 'Sprout',
  leaf: 'Leaf',
  bud: 'Bud',
  bloom: 'Bloom',
};
const BLOOM_GLYPH = '🌸';
const MAX_LINE_LENGTH = 64 * 1024;

/**
 * @typedef {(message: string, ...args: Array<string | number>) => string} Translate
 *
 * @typedef {{
 *   generation: number, stage: string, level: number, count: number | null,
 *   units_per_stage: number, is_bloom: boolean, unit_progress_percent: number,
 *   to_next_unit: number, to_next_stage: number, to_bloom: number
 * }} PlantView
 *
 * @typedef {{kind: 'stage_up', stage: string} | {kind: 'bloom'} | {kind: 'generation', generation: number}} Transition
 *
 * @typedef {{
 *   protocol: number, version: string, thinking: boolean, active_requests: number,
 *   live_tokens_estimate: number, glyph: string, view: PlantView,
 *   totals: {total: number, input: number, output: number, cache_creation: number, cache_read: number},
 *   last_request: {tokens: number, finished_at: string | null},
 *   transition: Transition | null, notice: string | null
 * }} Snapshot
 *
 * @typedef {{ok: true, snapshot: Snapshot} | {ok: false, reason: string, protocol?: unknown}} ParseResult
 */

/** @param {unknown} value */
const isCount = (value) => Number.isSafeInteger(value) && /** @type {number} */ (value) >= 0;

/** @param {unknown} value @param {number} max */
const isShortText = (value, max) => typeof value === 'string' && value.length <= max;

/** @param {unknown} value @returns {value is Record<string, unknown>} */
const isObject = (value) => typeof value === 'object' && value !== null && !Array.isArray(value);

/** @param {unknown} view */
function isPlantView(view) {
  if (!isObject(view)) return false;
  return (
    Number.isSafeInteger(view.generation) && /** @type {number} */ (view.generation) >= 1 &&
    STAGES.includes(/** @type {string} */ (view.stage)) &&
    Number.isSafeInteger(view.level) && /** @type {number} */ (view.level) >= 1 &&
    /** @type {number} */ (view.level) <= STAGES.length &&
    (view.count === null || isCount(view.count)) &&
    Number.isSafeInteger(view.units_per_stage) && /** @type {number} */ (view.units_per_stage) > 0 &&
    typeof view.is_bloom === 'boolean' &&
    isCount(view.unit_progress_percent) && /** @type {number} */ (view.unit_progress_percent) <= 100 &&
    isCount(view.to_next_unit) && isCount(view.to_next_stage) && isCount(view.to_bloom)
  );
}

/** @param {unknown} event */
function isTransition(event) {
  if (event === null) return true;
  if (!isObject(event)) return false;
  if (event.kind === 'bloom') return true;
  if (event.kind === 'stage_up') return STAGES.includes(/** @type {string} */ (event.stage));
  if (event.kind === 'generation') return Number.isSafeInteger(event.generation);
  return false;
}

/**
 * Parse and validate one feed line. Anything unexpected is rejected rather
 * than rendered: the view layer only ever sees well-formed numbers.
 * @param {string} line
 * @returns {ParseResult}
 */
function parseSnapshot(line) {
  if (typeof line !== 'string' || line.length > MAX_LINE_LENGTH) {
    return { ok: false, reason: 'oversized line' };
  }
  /** @type {unknown} */
  let data;
  try {
    data = JSON.parse(line);
  } catch {
    return { ok: false, reason: 'invalid JSON' };
  }
  if (!isObject(data)) return { ok: false, reason: 'not an object' };
  if (data.protocol !== PROTOCOL) return { ok: false, reason: 'protocol', protocol: data.protocol };
  const totals = data.totals;
  const last = data.last_request;
  const valid =
    isPlantView(data.view) &&
    typeof data.thinking === 'boolean' &&
    isCount(data.active_requests) &&
    isCount(data.live_tokens_estimate) &&
    isShortText(data.glyph, 16) &&
    isShortText(data.version, 32) &&
    isObject(totals) &&
    ['total', 'input', 'output', 'cache_creation', 'cache_read'].every((key) => isCount(totals[key])) &&
    isObject(last) &&
    isCount(last.tokens) &&
    (last.finished_at === null || isShortText(last.finished_at, 64)) &&
    isTransition(data.transition ?? null) &&
    (data.notice == null || isShortText(data.notice, 200));
  if (!valid) return { ok: false, reason: 'unexpected shape' };
  const snapshot = /** @type {Snapshot} */ (/** @type {unknown} */ (data));
  snapshot.transition = snapshot.transition ?? null;
  snapshot.notice = snapshot.notice ?? null;
  return { ok: true, snapshot };
}

const numberFormat = new Intl.NumberFormat('en-US');

/** @param {number} n */
function formatTokens(n) {
  return numberFormat.format(n);
}

/** @param {string} stage @param {Translate} t */
function stageName(stage, t) {
  return t(STAGE_LABELS[/** @type {keyof typeof STAGE_LABELS} */ (stage)] ?? STAGE_LABELS.seed);
}

/** Markdown-escape text that is not ours (the CLI's glyph, timestamps). @param {string} text */
function escapeMarkdown(text) {
  return text.replace(/[\\`*_{}[\]()#+\-.!|<>~]/g, '\\$&');
}

/**
 * The status bar text, mirroring the Claude Code status line:
 * `🌱 2/20` idle, `🌱 2/20 · 💧 67% · +5,000` while Claude is thinking.
 * @param {Snapshot} snap
 * @param {Translate} t
 * @param {{showPile?: boolean}} [options]
 */
function statusBarText(snap, t, options = {}) {
  const view = snap.view;
  let plant;
  if (view.is_bloom) {
    plant = `${snap.glyph} ${t('in bloom')}`;
  } else {
    const count = view.count ?? 0;
    const glyphs = options.showPile && count > 1 ? snap.glyph.repeat(count) : snap.glyph;
    plant = `${glyphs} ${count}/${view.units_per_stage}`;
  }
  if (!snap.thinking) return plant;
  return `${plant} · 💧 ${view.unit_progress_percent}% · +${formatTokens(snap.live_tokens_estimate)}`;
}

/** @param {Snapshot} snap @param {Translate} t */
function activityLine(snap, t) {
  if (!snap.thinking) return t('Idle — waiting for tokens');
  return snap.active_requests === 1
    ? t('Thinking — 1 request in flight')
    : t('Thinking — {0} requests in flight', snap.active_requests);
}

/** @param {Snapshot} snap @param {Translate} t */
function growthLines(snap, t) {
  const view = snap.view;
  if (view.is_bloom) {
    return {
      headline: t('In bloom'),
      next: t('The next tokens start generation {0}', view.generation + 1),
      bloom: '',
    };
  }
  return {
    headline: `${stageName(view.stage, t)} · ${view.count ?? 0}/${view.units_per_stage}`,
    next: t('{0} tokens to the next {1} ({2}%)', formatTokens(view.to_next_unit), stageName(view.stage, t), view.unit_progress_percent),
    bloom: t('{0} tokens to bloom', formatTokens(view.to_bloom)),
  };
}

/**
 * Localized text for a stage-up / bloom / new-generation event.
 * @param {Snapshot} snap
 * @param {Translate} t
 * @returns {string | null}
 */
function transitionText(snap, t) {
  const event = snap.transition;
  if (!event) return snap.notice;
  if (event.kind === 'generation') return t('{0} Bloomed! Generation {1} begins', BLOOM_GLYPH, event.generation);
  if (event.kind === 'bloom') return t('{0} In bloom!', BLOOM_GLYPH);
  return t('✨ Grew into {0}!', stageName(event.stage, t));
}

/**
 * Hover text for the status bar item (plain Markdown, never trusted).
 * @param {Snapshot} snap
 * @param {Translate} t
 */
function tooltipMarkdown(snap, t) {
  const view = snap.view;
  const growth = growthLines(snap, t);
  const totals = snap.totals;
  const glyph = escapeMarkdown(snap.glyph);
  const lines = [`**Token Sprout** · ${t('Generation {0}', view.generation)}`, ''];
  lines.push(`${glyph} **${growth.headline}**`, '');
  lines.push(`- ${growth.next}`);
  if (growth.bloom) lines.push(`- ${growth.bloom}`);
  lines.push('');
  const activity = activityLine(snap, t);
  lines.push(snap.thinking
    ? `💧 ${activity} · ${t('+{0} tokens estimated', formatTokens(snap.live_tokens_estimate))}`
    : activity);
  lines.push('');
  lines.push(t('Lifetime: {0} tokens', formatTokens(totals.total)));
  lines.push(t(
    'input {0} · output {1} · cache write {2} · cache read {3}',
    formatTokens(totals.input),
    formatTokens(totals.output),
    formatTokens(totals.cache_creation),
    formatTokens(totals.cache_read),
  ));
  lines.push('', `_${t('Click to open the plant view')}_`);
  return lines.join('\n');
}

/** @param {Snapshot} snap @param {Translate} t */
function accessibleLabel(snap, t) {
  const growth = growthLines(snap, t);
  const parts = ['Token Sprout', growth.headline, growth.next, activityLine(snap, t)];
  return parts.filter(Boolean).join(', ');
}

/**
 * @typedef {{kind: 'starting' | 'running' | 'missing' | 'outdated' | 'incompatible' | 'error', detail?: string}} FeedStatus
 */

/**
 * Title and detail for a feed problem, or null while healthy.
 * @param {FeedStatus} status
 * @param {Translate} t
 */
function problemText(status, t) {
  switch (status.kind) {
    case 'missing':
      return {
        title: t('token-sprout CLI not found'),
        detail: t('Install Token Sprout 0.2.0 or newer, or set tokenSprout.executablePath to its absolute path.'),
      };
    case 'outdated':
      return {
        title: t('token-sprout CLI is too old'),
        detail: t('This extension needs token-sprout 0.2.0 or newer (it reads token-sprout watch --json). Upgrade the CLI, then retry.'),
      };
    case 'incompatible':
      return {
        title: t('Incompatible token-sprout CLI'),
        detail: t('The CLI speaks a different feed protocol. Update the extension and the CLI to the same release.'),
      };
    case 'error':
      return {
        title: t('Plant feed stopped'),
        detail: t('{0}. Retrying automatically.', status.detail || t('The CLI exited')),
      };
    default:
      return null;
  }
}

/**
 * Everything the webview renders, already translated and formatted.
 * @param {FeedStatus} status
 * @param {Snapshot | null} snap
 * @param {string | null} notice
 * @param {Translate} t
 */
function viewModel(status, snap, notice, t) {
  const labels = {
    lifetime: t('Lifetime'),
    input: t('Input'),
    output: t('Output'),
    cacheWrite: t('Cache write'),
    cacheRead: t('Cache read'),
    setup: t('Open setup guide'),
    retry: t('Retry'),
    starting: t('Starting…'),
    footer: t('Read-only view of ~/.token-sprout/plant_state.json'),
    generation: snap ? t('Generation {0}', snap.view.generation) : '',
  };
  const problem = problemText(status, t);
  if (!snap || status.kind !== 'running') {
    return { state: problem ? 'problem' : 'starting', labels, problem, plant: null, notice: null };
  }
  const view = snap.view;
  const growth = growthLines(snap, t);
  return {
    state: 'running',
    labels,
    problem: null,
    notice,
    plant: {
      stage: view.stage,
      stageName: stageName(view.stage, t),
      isBloom: view.is_bloom,
      count: view.count ?? 0,
      units: view.units_per_stage,
      percent: view.unit_progress_percent,
      thinking: snap.thinking,
      headline: growth.headline,
      next: growth.next,
      bloom: growth.bloom,
      activity: activityLine(snap, t),
      estimate: snap.thinking ? t('+{0} tokens estimated', formatTokens(snap.live_tokens_estimate)) : '',
      totals: {
        total: formatTokens(snap.totals.total),
        input: formatTokens(snap.totals.input),
        output: formatTokens(snap.totals.output),
        cacheWrite: formatTokens(snap.totals.cache_creation),
        cacheRead: formatTokens(snap.totals.cache_read),
      },
      last: snap.last_request.finished_at
        ? t('Last reply: {0} tokens', formatTokens(snap.last_request.tokens))
        : t('No replies seen yet'),
      label: accessibleLabel(snap, t),
    },
  };
}

module.exports = {
  PROTOCOL,
  STAGES,
  parseSnapshot,
  formatTokens,
  statusBarText,
  tooltipMarkdown,
  accessibleLabel,
  transitionText,
  problemText,
  viewModel,
  escapeMarkdown,
};
