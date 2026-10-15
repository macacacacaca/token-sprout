// @ts-check
// Token Sprout sidebar script: renders the extension's ready-made view model.
// It only ever sets textContent, classes, and CSS custom properties.
(function () {
  'use strict';

  // @ts-ignore -- injected by VS Code into every webview
  const vscode = acquireVsCodeApi();
  const NOTICE_MS = 4000;
  const TRAY_CELLS = 20;

  /** @type {Record<string, string>} */
  const plants = JSON.parse(document.body.dataset.plants || '{}');

  /** @param {string} id */
  const $ = (id) => /** @type {HTMLElement} */ (document.getElementById(id));

  /** @param {string} id @param {unknown} text */
  const setText = (id, text) => {
    const element = document.getElementById(id);
    if (element) element.textContent = typeof text === 'string' ? text : '';
  };

  const garden = $('garden');
  const tray = $('tray');
  /** @type {HTMLLIElement[]} */
  const cells = [];
  for (let i = 0; i < TRAY_CELLS; i += 1) {
    const cell = document.createElement('li');
    tray.appendChild(cell);
    cells.push(cell);
  }

  /** @type {string | null} */
  let lastNotice = null;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let noticeTimer;

  $('setup').addEventListener('click', () => vscode.postMessage({ type: 'setup' }));
  $('retry').addEventListener('click', () => vscode.postMessage({ type: 'retry' }));

  window.addEventListener('message', (event) => {
    const message = event.data;
    if (message && message.type === 'model' && message.model) render(message.model);
  });

  /** @param {any} model */
  function render(model) {
    garden.dataset.state = model.state;
    const labels = model.labels || {};
    setText('setup', labels.setup);
    setText('retry', labels.retry);
    setText('starting', labels.starting);
    setText('footer', labels.footer);
    setText('lifetime-label', labels.lifetime);
    setText('input-label', labels.input);
    setText('output-label', labels.output);
    setText('cache-write-label', labels.cacheWrite);
    setText('cache-read-label', labels.cacheRead);
    setText('generation', labels.generation);

    if (model.problem) {
      setText('problem-title', model.problem.title);
      setText('problem-detail', model.problem.detail);
    }

    const plant = model.plant;
    if (!plant) {
      garden.classList.remove('thinking');
      return;
    }

    const image = /** @type {HTMLImageElement} */ ($('plant'));
    const source = plants[plant.stage];
    if (source && image.getAttribute('src') !== source) image.setAttribute('src', source);

    garden.dataset.stage = plant.stage;
    garden.classList.toggle('thinking', Boolean(plant.thinking));
    $('growth').setAttribute('aria-label', plant.label);

    const units = Math.min(Number(plant.units) || TRAY_CELLS, TRAY_CELLS);
    const count = Number(plant.count) || 0;
    const percent = Math.max(0, Math.min(100, Number(plant.percent) || 0));
    cells.forEach((cell, index) => {
      cell.hidden = index >= units;
      const state = plant.isBloom || index < count ? 'full' : index === count ? 'next' : '';
      cell.className = state;
      if (state === 'next') cell.style.setProperty('--p', `${percent}%`);
      else cell.style.removeProperty('--p');
    });
    $('meter').style.width = `${plant.isBloom ? 100 : percent}%`;

    setText('headline', plant.headline);
    setText('next', plant.next);
    setText('bloom', plant.bloom);
    setText('activity-text', plant.activity);
    setText('estimate', plant.estimate);
    setText('total', plant.totals.total);
    setText('input', plant.totals.input);
    setText('output', plant.totals.output);
    setText('cache-write', plant.totals.cacheWrite);
    setText('cache-read', plant.totals.cacheRead);
    setText('last', plant.last);

    const notice = typeof model.notice === 'string' ? model.notice : null;
    if (notice && notice !== lastNotice) celebrate(notice);
    lastNotice = notice;
  }

  /** @param {string} notice */
  function celebrate(notice) {
    const toast = $('toast');
    toast.textContent = notice;
    toast.classList.add('show');
    garden.classList.add('celebrate');
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => {
      toast.classList.remove('show');
      garden.classList.remove('celebrate');
    }, NOTICE_MS);
  }

  vscode.postMessage({ type: 'ready' });
})();
