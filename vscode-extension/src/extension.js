// @ts-check
'use strict';

const vscode = require('vscode');
const claudeCode = require('./claudeCode');
const format = require('./format');
const { PlantFeed } = require('./feed');
const { resolveExecutable } = require('./locate');
const { PlantViewProvider } = require('./webview');

const SETUP_GUIDE = 'https://github.com/macacacacaca/token-sprout#vs-code-extension';
const NOTICE_MS = 4_000;
const SUGGEST_DELAY_MS = 3_000;
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
  let suggested = false;

  const settings = () => vscode.workspace.getConfiguration('tokenSprout');
  const wrapperSettings = () => vscode.workspace.getConfiguration(claudeCode.WRAPPER_SECTION);

  /** @returns {import('./claudeCode').ConnectionState} */
  const connection = () =>
    claudeCode.connectionState({
      installed: Boolean(vscode.extensions.getExtension(claudeCode.CLAUDE_CODE_EXTENSION)),
      wrapper: wrapperSettings().get(claudeCode.WRAPPER_KEY),
    });

  const provider = new PlantViewProvider(context.extensionUri, (action) => {
    if (action === 'setup') void vscode.commands.executeCommand('tokenSprout.openSetupGuide');
    else if (action === 'connect') void vscode.commands.executeCommand('tokenSprout.connectClaudeCode');
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
    if (next.kind === 'running') setTimeout(() => void suggestConnection(), SUGGEST_DELAY_MS);
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

  /**
   * Offer once per window, and only while the plant feed works, to connect
   * the Claude Code extension; "Don't ask again" turns the offer off.
   */
  async function suggestConnection() {
    if (suggested || connection() !== 'disconnected') return;
    if (!settings().get('suggestClaudeCodeConnection', true)) return;
    suggested = true;
    const connect = t('Connect');
    const never = t("Don't ask again");
    const choice = await vscode.window.showInformationMessage(
      t("Conversations from the Claude Code extension don't reach Token Sprout yet, so they don't feed the plant. Connect them?"),
      connect,
      t('Not now'),
      never,
    );
    if (choice === connect) await vscode.commands.executeCommand('tokenSprout.connectClaudeCode');
    else if (choice === never) {
      await settings().update('suggestClaudeCodeConnection', false, vscode.ConfigurationTarget.Global);
    }
  }

  /** Point Claude Code's process wrapper at token-sprout-claude, after consent. */
  async function connectClaudeCode() {
    if (!vscode.extensions.getExtension(claudeCode.CLAUDE_CODE_EXTENSION)) {
      void vscode.window.showWarningMessage(t('The Claude Code extension is not installed.'));
      return;
    }
    const current = wrapperSettings().get(claudeCode.WRAPPER_KEY);
    if (claudeCode.isTokenSproutWrapper(current)) {
      void vscode.window.showInformationMessage(t('Claude Code is already connected to Token Sprout.'));
      return;
    }
    const cli = await resolveExecutable({ configured: settings().get('executablePath', '') });
    const wrapper = await claudeCode.resolveWrapper({ cli });
    if (!wrapper) {
      const guide = t('Open setup guide');
      const choice = await vscode.window.showWarningMessage(
        t('token-sprout-claude was not found. Install Token Sprout 0.2.1 or newer, then connect again.'),
        guide,
      );
      if (choice === guide) await vscode.commands.executeCommand('tokenSprout.openSetupGuide');
      return;
    }
    const details = [
      t('New Claude Code conversations in VS Code will start through {0}. It routes their API traffic through the local Token Sprout proxy (127.0.0.1) so the plant can grow, and starts Claude normally whenever the proxy is unavailable.', wrapper),
      t('Conversations that are already open keep running as they are until they restart.'),
    ];
    if (typeof current === 'string' && current.trim()) {
      details.push(t('Claude Code currently starts through {0}; connecting replaces that setting.', current));
    }
    const connect = t('Connect');
    const choice = await vscode.window.showInformationMessage(
      t('Connect Claude Code to Token Sprout?'),
      { modal: true, detail: details.join('\n\n') },
      connect,
    );
    if (choice !== connect) return;
    await wrapperSettings().update(claudeCode.WRAPPER_KEY, wrapper, vscode.ConfigurationTarget.Global);
    output.info(`connected Claude Code through ${wrapper}`);
    void vscode.window.showInformationMessage(t('Connected. New Claude Code conversations will feed your plant.'));
  }

  /** Remove the process wrapper, but only when it is ours. */
  async function disconnectClaudeCode() {
    const current = wrapperSettings().get(claudeCode.WRAPPER_KEY);
    if (!claudeCode.isTokenSproutWrapper(current)) {
      void vscode.window.showInformationMessage(t("Claude Code isn't connected to Token Sprout; nothing to change."));
      return;
    }
    await wrapperSettings().update(claudeCode.WRAPPER_KEY, undefined, vscode.ConfigurationTarget.Global);
    output.info('disconnected Claude Code');
    void vscode.window.showInformationMessage(t('Disconnected. New Claude Code conversations start without Token Sprout.'));
  }

  function render() {
    const link = connection();
    provider.update(format.viewModel(status, snapshot, notice, t, link));

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
      item.tooltip = new vscode.MarkdownString(format.tooltipMarkdown(snapshot, t, link));
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
    vscode.commands.registerCommand('tokenSprout.connectClaudeCode', connectClaudeCode),
    vscode.commands.registerCommand('tokenSprout.disconnectClaudeCode', disconnectClaudeCode),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration('tokenSprout.executablePath')) feed.restart();
      else if (
        event.affectsConfiguration('tokenSprout.statusBar') ||
        event.affectsConfiguration(`${claudeCode.WRAPPER_SECTION}.${claudeCode.WRAPPER_KEY}`)
      ) {
        render();
      }
    }),
    vscode.extensions.onDidChange(() => render()),
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
    claudeCodeConnection: connection,
  };
}

function deactivate() {}

module.exports = { activate, deactivate };
