// @ts-check
'use strict';

const vscode = require('vscode');
const format = require('./format');
const { PlantFeed } = require('./feed');
const { resolveExecutable } = require('./locate');
const { PlantViewProvider } = require('./webview');

const SETUP_GUIDE = 'https://github.com/macacacacaca/token-sprout#vs-code-extension';
const NOTICE_MS = 4_000;
// Lowest priority = right-most: the plant sits in the window's corner.
const STATUS_PRIORITY = -1_000;

/** @type {format.Translate} */
const t = (message, ...args) => vscode.l10n.t(message, ...args);

/** @param {vscode.ExtensionContext} context */
function activate(context) {
  const output = vscode.window.createOutputChannel('Token Sprout', { log: true });
  const item = vscode.window.createStatusBarItem(
    'tokenSprout.plant',
    vscode.StatusBarAlignment.Right,
    STATUS_PRIORITY,
  );
  item.name = 'Token Sprout';

  /** @type {format.FeedStatus} */
  let status = { kind: 'starting' };
  /** @type {format.Snapshot | null} */
  let snapshot = null;
  /** @type {string | null} */
  let notice = null;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let noticeTimer;
  let itemVisible = false;

  const settings = () => vscode.workspace.getConfiguration('tokenSprout');

  const provider = new PlantViewProvider(context.extensionUri, (action) => {
    if (action === 'setup') void vscode.commands.executeCommand('tokenSprout.openSetupGuide');
    else feed.restart();
  });

  const feed = new PlantFeed({
    resolveExecutable: () => resolveExecutable({ configured: settings().get('executablePath', '') }),
    log: (message) => output.info(message),
  });

  feed.on('status', (next) => {
    if (next.kind !== status.kind) output.info(`feed status: ${next.kind}`);
    status = next;
    render();
  });
  feed.on('snapshot', (next) => {
    snapshot = next;
    const text = format.transitionText(next, t);
    if (text) showNotice(text);
    render();
  });

  /** @param {string} text */
  function showNotice(text) {
    notice = text;
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => {
      notice = null;
      render();
    }, NOTICE_MS);
  }

  /** @param {boolean} visible */
  function setVisible(visible) {
    itemVisible = visible;
    if (visible) item.show();
    else item.hide();
  }

  function render() {
    provider.update(format.viewModel(status, snapshot, notice, t));

    const visibility = String(settings().get('statusBar.visibility') ?? 'always');
    if (visibility === 'hidden') {
      setVisible(false);
      return;
    }
    item.backgroundColor = undefined;
    if (status.kind === 'running' && snapshot) {
      if (visibility === 'thinking' && !snapshot.thinking && !notice) {
        setVisible(false);
        return;
      }
      const showPile = settings().get('statusBar.showPile', false);
      item.text = notice ?? format.statusBarText(snapshot, t, { showPile });
      item.tooltip = new vscode.MarkdownString(format.tooltipMarkdown(snapshot, t));
      item.accessibilityInformation = { label: format.accessibleLabel(snapshot, t) };
      item.command = 'tokenSprout.showPlant';
      setVisible(true);
      return;
    }
    const problem = format.problemText(status, t);
    if (!problem) {
      // Starting up: stay quiet unless a plant was already on screen.
      if (!snapshot) setVisible(false);
      return;
    }
    item.text = '$(warning) Token Sprout';
    item.tooltip = new vscode.MarkdownString(`**${problem.title}**\n\n${format.escapeMarkdown(problem.detail)}`);
    item.accessibilityInformation = { label: `Token Sprout: ${problem.title}` };
    item.command = status.kind === 'error' ? 'tokenSprout.restart' : 'tokenSprout.openSetupGuide';
    item.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    setVisible(true);
  }

  context.subscriptions.push(
    output,
    item,
    vscode.window.registerWebviewViewProvider('tokenSprout.plant', provider),
    vscode.commands.registerCommand('tokenSprout.showPlant', () =>
      vscode.commands.executeCommand('tokenSprout.plant.focus'),
    ),
    vscode.commands.registerCommand('tokenSprout.restart', () => feed.restart()),
    vscode.commands.registerCommand('tokenSprout.openSetupGuide', () =>
      vscode.env.openExternal(vscode.Uri.parse(SETUP_GUIDE)),
    ),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration('tokenSprout.executablePath')) feed.restart();
      else if (event.affectsConfiguration('tokenSprout.statusBar')) render();
    }),
    {
      dispose: () => {
        feed.stop();
        if (noticeTimer) clearTimeout(noticeTimer);
      },
    },
  );

  feed.start();

  // A small read-only surface for the integration tests.
  return {
    statusBarText: () => (itemVisible ? item.text : null),
    feedStatus: () => status.kind,
    viewResolved: () => provider.resolved,
  };
}

function deactivate() {}

module.exports = { activate, deactivate };
