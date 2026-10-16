# Changelog

## 0.2.1

- **Connect Claude Code** (new command, also offered once per window and in
  the sidebar): conversations started from Anthropic's Claude Code extension
  now feed the plant. It sets `claudeCode.claudeProcessWrapper` to
  Token Sprout's `token-sprout-claude` after you confirm; **Disconnect Claude
  Code** removes it. Needs the CLI 0.2.1 or newer.
- The status bar tooltip and sidebar say when Claude Code in VS Code is not
  connected.

## 0.2.0

- First release: the Token Sprout plant in the VS Code status bar and an
  illustrated sidebar view, fed by `token-sprout watch --json` (CLI 0.2.0+).
- Dawn and night scenes that follow the color theme; watering animation
  while Claude is thinking; stage-up notices; reduced-motion support.
- English and Traditional Chinese.
