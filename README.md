# Token Sprout 🌿

[English](README.md) | [繁體中文](README.zh-TW.md)

Grow a tiny terminal plant with the tokens you spend in Claude Code.

Token Sprout is a local, transparent pass-through proxy. It observes token
usage metadata in Anthropic API responses and turns it into plant growth. Your
normal command stays `claude`; the plant appears in
[Claude Code's bottom status line](https://code.claude.com/docs/en/statusline)
while Claude is thinking.

![How Token Sprout works](docs/token-sprout-overview.svg)

> **Status: `v0.1.0` released.** Automated tests, package builds, a clean
> `pipx install .`, and macOS real-session checks have passed — API-key and
> subscription-OAuth sessions, tool use, Esc cancellation, and a manual
> direct-vs-proxy streaming comparison. `pipx` is the supported install path.
> Linux shell behavior is covered by CI; WSL2 remains experimental. Growth
> tuning, `uvx` validation, and a demo GIF are post-release follow-ups.

## Install from GitHub

### Requirements

- macOS (manually verified), Linux (CI-covered), or experimental Windows
  10/11 support through WSL2
- zsh or bash (automatic shell integration does not support fish yet)
- Python 3.10 or newer: `python3 --version`
- Claude Code already installed: `claude --version`
- `pipx` is recommended so Token Sprout stays isolated from system Python

Native Windows PowerShell and Command Prompt are not supported. The WSL2 path
below is experimental: it is documented for early users but has not yet
completed an end-to-end check on a real Windows machine.

On macOS or Linux, install `pipx` first if needed. Windows users should skip
to the WSL2 section below instead:

```bash
# macOS with Homebrew
brew install pipx
pipx ensurepath

# Linux
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

Open a new terminal after `pipx ensurepath`.

### Windows 10/11 through WSL2 (experimental)

Install and launch Claude Code, Python, and Token Sprout inside the **same
WSL2 distribution**. Do not install Token Sprout in WSL and then launch the
native Windows `claude.exe`; that process will not use the WSL shell wrapper.

First open **PowerShell as Administrator** and install WSL2:

```powershell
wsl --install
```

Restart Windows, open PowerShell again, and confirm that Ubuntu is using
version 2:

```powershell
wsl --list --verbose
```

If Ubuntu shows version 1, convert it before continuing:

```powershell
wsl --set-version Ubuntu 2
```

Open the Ubuntu app and install the Linux prerequisites:

```bash
sudo apt update
sudo apt install -y python3 python3-venv pipx git curl
pipx ensurepath
```

Close Ubuntu, reopen it, and install Claude Code **inside WSL**:

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude --version
```

Download and extract this repository. Windows files are visible inside WSL
under `/mnt/c`; for example, a ZIP extracted to the Windows Downloads folder
is usually reached with:

```bash
cd "/mnt/c/Users/YOUR_WINDOWS_USER/Downloads/token-sprout-main"
```

Use the actual extracted folder containing `pyproject.toml`, then continue
with steps 1 and 2 below. Run every Token Sprout and `claude` command inside
WSL. The private runtime directory will be the Linux path
`~/.token-sprout/`, where `0700`/`0600` permissions work normally.

For VS Code, install VS Code on Windows plus the
[WSL extension](https://code.visualstudio.com/docs/remote/wsl), then run
`code .` from the WSL project folder. New terminals in that VS Code window
will run inside WSL. See Microsoft's
[WSL installation guide](https://learn.microsoft.com/windows/wsl/install)
and Claude Code's
[Windows/WSL instructions](https://code.claude.com/docs/en/installation) for
the platform prerequisites.

### 1. Download and install

On GitHub, choose **Code → Download ZIP**, extract it, then open a terminal in
the extracted repository folder—the folder containing `pyproject.toml`.

```bash
cd /path/to/downloaded/token-sprout
pipx install .
token-sprout --version
```

If the Python chosen by `pipx` is older than 3.10, select a newer interpreter:

```bash
pipx install --python python3.13 .
```

### 2. Initialize and connect Claude Code

Run these once:

```bash
token-sprout init
token-sprout install-claude
token-sprout install-statusline
```

Then close that terminal, open a new one, and launch Claude normally:

```bash
claude
```

That is the complete setup. While Claude is using tokens, the bottom status
line looks like this:

```text
🌱🌱 2/20 · 💧 [██████░░░░] 67% · +5,000 tokens
```

The line hides when Claude is idle. Use
`token-sprout install-statusline --always` if you prefer a compact plant to
remain visible. Claude Code must trust the workspace before it runs a status
line command; interact once or restart Claude Code after installation.

## What the installers change

`install-claude` adds one marked, removable shell function:

- zsh: `$ZDOTDIR/.zshrc` when `ZDOTDIR` is set, otherwise `~/.zshrc`
- bash on macOS: `~/.bash_profile`
- bash on Linux: `~/.bashrc`

The function stores the absolute paths of `token-sprout` and the existing
Claude executable. It does not replace Claude or save an API URL or
credential. If an alias or function named `claude` already exists—including
one loaded from another sourced file—Token Sprout leaves it unchanged and
prints a warning. Review the conflict before using `--force`.

`install-statusline` merges this project's command into
`~/.claude/settings.json`, preserves all other settings, and self-tests the
command before writing. It refuses to replace a status line it does not own
unless you explicitly pass `--force`.

## Daily use

Just use Claude Code normally:

```bash
claude
claude --resume
```

The managed function calls `token-sprout run -- <absolute Claude path>`. For
that child process only, `run` sets the local API base URL and enables Claude
Code's dynamic tool search when you have not chosen an explicit
`ENABLE_TOOL_SEARCH` value. It starts the localhost proxy if needed and stops
only a proxy it started. It never prints the command arguments, which may
contain a private prompt.

Useful commands:

```bash
token-sprout status       # one-shot plant and token summary
token-sprout watch        # animated panel in another terminal
token-sprout reset        # start again after confirmation
```

The animated panel needs a terminal at least 44×12:

```text
╭──── Token Sprout 🌿  generation 1 ────╮
│      💧            💧                 │
│           💧                          │
│ 🌱 🌱 🌱 🌱                           │
│ sprout  ·  4/20                       │
│ Thinking... (1 request in flight)     │
╰───────────── Ctrl+C to exit ─────────╯
```

### Optional `/plant` skill

Tracking must begin when Claude launches; `/plant` is only a convenient
read-only [Claude Code skill](https://code.claude.com/docs/en/slash-commands).
Create `~/.claude/skills/plant/SKILL.md` with:

```markdown
---
name: plant
description: Show my Token Sprout plant status
allowed-tools: Bash(token-sprout status)
---

## Current plant

!`token-sprout status`

Summarize the plant status above in one friendly sentence.
```

Restart Claude Code, then enter `/plant`.

### Explicit or manual launch

The shell installer is optional. You can always run:

```bash
token-sprout run -- claude
token-sprout run --port 8100 -- claude --resume
```

For debugging with two terminals:

```bash
# terminal 1
token-sprout proxy --port 8000

# terminal 2 (this terminal only)
ANTHROPIC_BASE_URL=http://127.0.0.1:8000 ENABLE_TOOL_SEARCH=true claude
```

Do not save `ANTHROPIC_BASE_URL` in your shell configuration. Claude will be
unable to connect whenever the proxy is not running.

## Growth rules

Only successful `POST /v1/messages` usage feeds the plant:

| Usage field | Tracked | Feeds growth |
|---|---:|---:|
| `input_tokens` | ✅ | ✅ |
| `output_tokens` | ✅ | ✅ |
| `cache_creation_input_tokens` | ✅ | ✅ |
| `cache_read_input_tokens` | ✅ | ❌ |

Cache reads are displayed but excluded from food because Claude Code can read
millions of cached tokens in a day. These numbers are a game mechanic, not a
billing ledger. `/v1/messages/count_tokens` is also excluded because it is an
estimate, not real consumption.

Every **10,000 food tokens** creates one seed. Twenty units of the current
stage merge into one unit of the next stage:

```text
🌰×20 → 🌱×1  ·  🌱×20 → 🪴×1  ·  🪴×20 → 🌷×1  ·  🌷×20 → 🌸
```

Growth is cumulative across the entire generation: `current_exp` does not
reset when units merge. One seed costs 10,000 food tokens; one sprout costs
200,000; one leaf plant costs 4,000,000; and one bud costs 80,000,000. Bloom
takes **1,600,000,000 food tokens** (`10,000 × 20⁴`). Bloom remains on
screen; the next feeding starts a new generation and resets only that
generation's progress. Lifetime token totals remain. The live
status bar estimates progress toward the next unit of the current stage (~4
characters per token); the plant commits only exact usage received when a
response finishes.

## Security model

With the documented default upstream, requests go only to the same Anthropic
API endpoint Claude Code already uses.

- The proxy binds only to `127.0.0.1`.
- Request and response bodies pass through memory and are never stored or
  logged.
- `x-api-key` and `authorization` headers are forwarded unchanged and are
  never parsed, logged, or persisted.
- Forwarding uses raw bytes, preserves duplicate response headers, does not
  retry upstream errors, and does not wait for plant-state disk writes.
- Before reusing a listener, `run` verifies a fresh, port-bound HMAC proof.
- Request paths in logs have control characters escaped and are length-limited.

Token Sprout keeps this private directory at mode `0700`; all files below are
mode `0600`, including after upgrading an older installation:

| Path under `~/.token-sprout/` | Contents |
|---|---|
| `plant_state.json` | Usage counters, plant state, and timestamps |
| `plant_state.json.corrupt` | Quarantined copy of a damaged state file, kept for manual recovery (only if damage ever occurs) |
| `proxy.log` | Method, sanitized path, status, and duration; no bodies or headers |
| `proxy.secret` | Random secret for local proxy identity checks |
| `state.lock` | State update lock |

Optional writes outside that directory happen only when you run an installer:

- the marked shell block described above;
- the `statusLine` object in `~/.claude/settings.json`.

A malicious process running under your own OS account is outside this threat
boundary because it can already read that account's Claude credentials. See
[SECURITY.md](SECURITY.md) to report a vulnerability privately.

## Known limitations

- v0.1 is validated for Claude Code; other Anthropic clients may work but are
  not claimed as supported.
- Automatic shell setup supports zsh and bash on macOS and is exercised on
  Linux in CI. Native Windows PowerShell/CMD and fish are not supported;
  Windows through WSL2 remains an experimental, not yet end-to-end verified
  path.
- Per Claude Code's
  [environment-variable behavior](https://code.claude.com/docs/en/env-vars),
  **Remote Control is unavailable** while a non-first-party base URL such as
  the localhost proxy is active. Dynamic tool search remains enabled unless
  you explicitly set `ENABLE_TOOL_SEARCH` yourself.
- Forwarding is designed and tested not to buffer responses. A manual macOS
  direct-vs-proxy session showed no repeatable visible delay or batching, and
  Esc cancellation completed normally. `v0.1.0` does not include a published
  instrumented latency benchmark.

## Troubleshooting

**`token-sprout: command not found`**

Run `pipx ensurepath`, open a new terminal, and try again. Confirm with
`pipx list`. If necessary, reinstall with
`pipx install --python python3.13 .` from the downloaded folder.

**An existing `claude` alias/function was left unchanged**

Run `type claude` in a new shell and inspect your shell startup files,
including files sourced by `.zshrc` or `.bashrc`. Remove or rename the
conflicting override, then rerun `token-sprout install-claude`. Use `--force`
only when you intentionally want Token Sprout to replace it at shell runtime.

**Port 8000 is already in use**

Token Sprout refuses to pass credentials to an unverified listener. Stop the
other service or launch explicitly with
`token-sprout run --port 8100 -- claude`.

**The status line does not appear**

Make sure the workspace is trusted, interact once or restart Claude Code, then
run `token-sprout install-statusline` again. If the installation moved, this
command discovers and writes its new absolute executable path.

**Claude cannot reach the API**

Do not export `ANTHROPIC_BASE_URL` globally. Start a fresh terminal and launch
through plain `claude` after installing the integration. Check
`~/.token-sprout/proxy.log`; it never contains prompts or credentials.

## Uninstall

Remove integrations before removing the executable:

```bash
token-sprout uninstall-statusline
token-sprout uninstall-claude
pipx uninstall token-sprout
```

These commands preserve Claude Code and every unrelated setting. If you also
want to erase your plant history, manually delete `~/.token-sprout/`. Delete
`~/.claude/skills/plant/` separately if you created the optional skill.

## Development

Use Python 3.10 or newer; the repository's development environment uses 3.13:

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -W error
```

CI tests Python 3.10–3.13, treats warnings as errors, compiles the source,
checks installed dependencies, audits them, builds a wheel, and smoke-tests a
clean wheel installation.

| File | Responsibility |
|---|---|
| `token_sprout/proxy.py` | Transparent raw-byte forwarding and fail-open usage tee |
| `token_sprout/usage_parser.py` | JSON and incremental SSE usage extraction |
| `token_sprout/state.py` | Private files, single-writer state, lock, atomic replace |
| `token_sprout/game.py` | Token-to-growth rules |
| `token_sprout/ui.py`, `ascii_art.py` | Read-only status and animation views |
| `token_sprout/cli.py` | CLI, lifecycle, shell integration, and statusline installer |

The repository's [`docs/technical-spec.md`](docs/technical-spec.md) is the
authoritative v0.1 technical specification and takes precedence over
implementation decisions.

## License

MIT
