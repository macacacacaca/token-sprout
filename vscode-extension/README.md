# Token Sprout for VS Code

Watch your Claude Code tokens grow a tiny plant — in the corner of the VS
Code status bar and in an illustrated sidebar.

![Token Sprout's sidebar in dark and light themes, with the status bar item](https://raw.githubusercontent.com/macacacacaca/token-sprout/v0.2.1/docs/assets/vscode-preview.png)

This extension is the editor view of [Token Sprout](https://github.com/macacacacaca/token-sprout),
a local pass-through proxy that turns Claude Code's token usage into plant
growth. It shows the same plant as the Claude Code status line and the
terminal panel, because every number comes from the same CLI.

## Requirements

- The `token-sprout` CLI **0.2.1 or newer**, installed and connected to
  Claude Code. Follow the [setup guide](https://github.com/macacacacaca/token-sprout#install-from-github).
- Using Claude Code's own VS Code extension? Run **Token Sprout: Connect
  Claude Code** once (Token Sprout offers it too) so those conversations go
  through the proxy and feed the plant.
- VS Code 1.90 or newer.

With Remote - SSH, WSL, or Dev Containers, the extension runs where your
workspace runs, so install the CLI on that side too.

## What you get

- **Status bar** — `🌱 2/20` while idle; `🌱 2/20 · 💧 67% · +5,000` while
  Claude is thinking; a short notice when the plant grows into a new stage.
  Hover for the full breakdown; click to open the sidebar.
- **Sidebar** — the plant in a dawn scene (light themes) or a night scene
  (dark themes), watered while Claude thinks, with a 20-cell tray that fills
  toward the next 20:1 merge and your lifetime token totals.
- English and Traditional Chinese, following VS Code's display language.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `tokenSprout.executablePath` | empty | Absolute path to `token-sprout`. Empty searches absolute `PATH` entries, then `~/.local/bin`. Machine-scoped: workspace settings cannot change it. |
| `tokenSprout.statusBar.visibility` | `always` | `always`, `thinking` (only while Claude is thinking), or `hidden`. |
| `tokenSprout.statusBar.showPile` | `false` | Repeat the stage emoji once per unit (`🌱🌱🌱 3/20`). |
| `tokenSprout.suggestClaudeCodeConnection` | `true` | Offer to connect Claude Code's extension when its conversations bypass Token Sprout. |

Commands: **Token Sprout: Show Plant**, **Restart Plant Feed**, **Open Setup
Guide**, **Connect Claude Code**, and **Disconnect Claude Code**.

**Connect Claude Code** sets Claude Code's `claudeCode.claudeProcessWrapper`
to the CLI's `token-sprout-claude` after you confirm. That wrapper verifies the
local proxy and starts Claude through it — or, when it cannot, starts Claude
unchanged. While a wrapper is set, Claude Code starts new conversations in the
permission mode you last picked (Manual if you never picked one).

## Privacy and security

- Read-only. The extension starts exactly one program — your
  `token-sprout watch --json` — without a shell, and reads its output. It
  never opens `~/.token-sprout/` itself and writes no files. It changes VS
  Code settings only when you choose Connect / Disconnect Claude Code or
  Don't ask again.
- No network requests and no telemetry. Prompts, replies, and credentials
  never reach the extension; the feed carries token counts only.
- The sidebar webview runs under a strict Content Security Policy and loads
  only images, styles, and the one script bundled in this extension.
- Every feed line is validated before display; malformed lines are dropped.

## License

MIT
