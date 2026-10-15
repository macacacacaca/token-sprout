// @ts-check
'use strict';

const crypto = require('node:crypto');
const vscode = require('vscode');
const { STAGES } = require('./format');

/** Messages the webview may send; anything else is ignored. */
const WEBVIEW_MESSAGES = new Set(['ready', 'setup', 'retry']);

/**
 * The illustrated sidebar view. It renders a ready-made view model (see
 * format.viewModel) and can only ask the extension to retry or open the
 * setup guide.
 * @implements {vscode.WebviewViewProvider}
 */
class PlantViewProvider {
  /**
   * @param {vscode.Uri} extensionUri
   * @param {(message: 'setup' | 'retry') => void} onAction
   */
  constructor(extensionUri, onAction) {
    this.extensionUri = extensionUri;
    this.onAction = onAction;
    /** @type {vscode.WebviewView | undefined} */
    this.view = undefined;
    /** @type {unknown} */
    this.model = null;
    this.resolved = false;
  }

  /** @param {vscode.WebviewView} webviewView */
  resolveWebviewView(webviewView) {
    this.view = webviewView;
    const media = vscode.Uri.joinPath(this.extensionUri, 'media');
    webviewView.webview.options = { enableScripts: true, localResourceRoots: [media] };
    webviewView.webview.html = this.html(webviewView.webview, media);
    webviewView.webview.onDidReceiveMessage((message) => {
      const type = message && typeof message === 'object' ? message.type : undefined;
      if (!WEBVIEW_MESSAGES.has(type)) return;
      if (type === 'ready') this.post();
      else this.onAction(type);
    });
    webviewView.onDidChangeVisibility(() => this.post());
    webviewView.onDidDispose(() => {
      this.view = undefined;
    });
    this.resolved = true;
  }

  /** @param {unknown} model */
  update(model) {
    this.model = model;
    this.post();
  }

  post() {
    if (this.view && this.view.visible && this.model) {
      void this.view.webview.postMessage({ type: 'model', model: this.model });
    }
  }

  /**
   * @param {vscode.Webview} webview
   * @param {vscode.Uri} media
   */
  html(webview, media) {
    const nonce = crypto.randomBytes(16).toString('base64');
    /** @param {...string} parts */
    const asset = (...parts) => webview.asWebviewUri(vscode.Uri.joinPath(media, ...parts)).toString();
    const plants = Object.fromEntries(STAGES.map((stage) => [stage, asset('plants', `${stage}.svg`)]));
    const csp = [
      "default-src 'none'",
      `img-src ${webview.cspSource}`,
      `style-src ${webview.cspSource}`,
      `script-src 'nonce-${nonce}'`,
    ].join('; ');
    const lang = vscode.env.language.toLowerCase().startsWith('zh') ? 'zh-Hant' : 'en';
    return `<!DOCTYPE html>
<html lang="${lang}">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${asset('plant.css')}">
<title>Token Sprout</title>
</head>
<body data-plants="${escapeAttribute(JSON.stringify(plants))}">
<main class="garden" id="garden" data-state="starting">
  <section class="scene" aria-hidden="true">
    <div class="rain">
      ${[1, 2, 3, 4].map((n) => `<svg class="drop drop-${n}" viewBox="-9 -15 18 28"><path d="M0,-14C5,-6 8,-1 8,4A8,8 0 1 1 -8,4C-8,-1 -5,-6 0,-14Z"/><ellipse cx="-3" cy="3" rx="2" ry="3.4"/></svg>`).join('')}
    </div>
    <img class="plant" id="plant" alt="" src="${plants.seed}">
    <div class="burst"><span></span><span></span><span></span><span></span></div>
  </section>
  <p class="toast" id="toast" role="status" aria-live="polite"></p>
  <section class="card growth" id="growth">
    <div class="growth-head">
      <h2 id="headline"></h2>
      <span class="generation" id="generation"></span>
    </div>
    <ol class="tray" id="tray"></ol>
    <div class="meter" aria-hidden="true"><div class="meter-fill" id="meter"></div></div>
    <p class="next" id="next"></p>
    <p class="muted" id="bloom"></p>
  </section>
  <section class="card activity" id="activity">
    <p class="activity-line"><span class="pulse" aria-hidden="true"></span><span id="activity-text"></span></p>
    <p class="muted" id="estimate"></p>
  </section>
  <section class="card totals" id="totals">
    <h3 id="lifetime-label"></h3>
    <p class="total" id="total"></p>
    <dl>
      <dt id="input-label"></dt><dd id="input"></dd>
      <dt id="output-label"></dt><dd id="output"></dd>
      <dt id="cache-write-label"></dt><dd id="cache-write"></dd>
      <dt id="cache-read-label"></dt><dd id="cache-read"></dd>
    </dl>
    <p class="muted" id="last"></p>
  </section>
  <section class="card problem" id="problem">
    <h2 id="problem-title"></h2>
    <p id="problem-detail"></p>
    <div class="actions">
      <button type="button" id="setup"></button>
      <button type="button" class="secondary" id="retry"></button>
    </div>
  </section>
  <p class="starting muted" id="starting"></p>
  <footer class="muted" id="footer"></footer>
</main>
<script nonce="${nonce}" src="${asset('plant.js')}"></script>
</body>
</html>`;
  }
}

/** @param {string} value */
function escapeAttribute(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

module.exports = { PlantViewProvider, escapeAttribute };
