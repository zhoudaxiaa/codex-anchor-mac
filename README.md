# codex-anchor-mac

[![English](https://img.shields.io/badge/English-blue?style=for-the-badge)](README.md)
[![简体中文](https://img.shields.io/badge/简体中文-lightgrey?style=for-the-badge)](README.zh-CN.md)

Anchor your ChatGPT subscription's 5-hour usage window at a time you choose, so
the window boundaries fall **inside** your working day instead of after it.

> **This does not add quota and does not bypass any limit.** It sends one
> deliberately tiny request so the rolling window *starts* at a useful moment.
> You get the same amount of usage per window as everyone else.

**For ChatGPT Plus, on macOS.** Two deliberate limits:

- **Plus, not Pro.** Plus got the 5-hour window back on 25 Aug 2026;
  [OpenAI's pricing docs](https://developers.openai.com/codex/pricing) say Pro
  plans currently have no five-hour limit at all. On Pro there is no window to
  anchor and the weekly cap is what binds, so this tool does nothing for you.
- **macOS, not cross-platform.**

---

## The problem

Codex, Work, workspace agents and ChatGPT for Excel share a usage pool that
resets on a rolling 5-hour window. The window starts at your **first** request,
not on a clock boundary.

So if you sit down at 08:00 and burn through a window's quota in two hours:

```
08:00  first message  ->  window runs 08:00-13:00
10:00  quota exhausted
10:00-13:00  three hours of waiting
```

A anchor request before you wake up shifts the boundaries earlier, which puts
*more* boundaries inside your working day:

```
05:00  anchor (a few seconds, negligible quota)  ->  window runs 05:00-10:00
08:00  you start work with a full, untouched window
10:00  quota exhausted -- and the window rolls over at the same moment
10:00  new window 10:00-15:00, no waiting
```

## Picking your time

```
anchor time = wake-up time + how long a window lasts you - 5h
```

Wake at 08:00 and exhaust a window in 2h? `08:00 + 2h - 5h = 05:00`.
Wake at 07:30 with the same burn rate? `04:30`.

Setting it **too early** is worse than not setting it at all: the first window
expires before you can use much of it, and you waste the part you slept through.

## Why macOS specifically

Most anchoring tools are a cron line. On macOS that quietly does not work, in two
ways that produce **no error** — just a job that silently misses its time.

**1. TCC blocks launchd from protected folders.** Put the script in
`~/Documents`, `~/Desktop` or `~/Downloads` and launchd fails with
`Operation not permitted` (exit 126). Installation looks fine, because your
terminal has access and launchd does not. `install.sh` refuses to install from
those folders.

**2. A scheduled wake lands in DarkWake.** `pmset` can wake a sleeping Mac, but
it wakes into DarkWake, which honours "Sleep Service Back to Sleep" over an
idle assertion and freezes the request mid-flight. Measured on one machine over
ten nights:

| Power | Nights | Trigger set to | Request actually sent |
|---|---|---|---|
| AC | 5 | 04:58 | 05:02 – 05:14 |
| Battery | 3 | 04:58 | 05:27, 06:17, **06:56** |

A two-hour drift, with a log line that still says success. This tool forces a
real wake with `caffeinate -u`, then blanks the display again so nothing lights
up at 4am.

## Install

### Make the Codex CLI available in Terminal

Run the installation and registration commands below in **macOS Terminal**.
Installing the desktop app does not necessarily add `codex` to your shell's
`PATH`. If you see `command not found: codex`, locate the bundled executable.
For example, if it is at:

```text
/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex
```

add its directory to `PATH` and verify that the command works:

```bash
export PATH="/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin:$PATH"
codex --version
```

Continue once a version number appears. The path depends on your app version
and installation location; replace the example with your actual directory.

`export` affects the current terminal session. To keep it for future zsh
sessions, add the same `export PATH=...` line to `~/.zshrc`, then run
`source ~/.zshrc`. This configures your shell; it does not set a system-wide
environment variable for every desktop app.

### This fork (0.1.7)

A preparation LaunchAgent starts at the wake time (three minutes before the
request), holds an awake assertion while waiting, and releases it when the
request exits. The request's calendar time and Run now behavior are unchanged.
Preparation has a bounded timeout if the request never starts or hangs. Its
logs are `~/.codex-anchor/prepare.out.log` and `prepare.err.log`; request starts
and results are recorded in `anchor.log`. Both jobs are updated, enabled,
disabled, and removed together. Battery/DarkWake behavior still needs testing
on the target Mac; macOS can override assertions for forced sleep or low battery.

This fork fixes administrator authorization when changing the wake time. If
unattended `sudo` fails, macOS asks for authorization using its system dialog
(up to 120 seconds). Cancelling keeps a visible partial-failure message.

The panel's **Request settings** card selects the model and reasoning effort
for both scheduled and manual runs. Models and supported effort levels are
read from the CLI's configured local model catalogue (or `models_cache.json`).
Settings are saved separately from the schedule, without sending a request or
asking for wake authorization. Existing installs keep the CLI's default model
and `low` effort until changed. Choosing **Follow Codex default** omits that
override from the command. The settings live in `~/.codex-anchor/config.json`
as `model` and `reasoning_effort`; an empty string means no override.

Anchor requests use a short dedicated instruction file and disable local skill
catalogues, MCP, apps, plugins, memory, web search, and optional execution tools
through invocation-only overrides. Your normal Codex configuration is unchanged.
The CLI can still inject global AGENTS.md and some built-in tool/protocol
instructions; this is a smaller request, not an empty context.

For local development, build and install this checkout as the marketplace:

```bash
cd plugins/codex-anchor-mac/ui
npm ci
npm run build
cd ../../..
codex plugin marketplace add "$PWD"
codex plugin add codex-anchor-mac@codex-anchor-mac
python3 plugins/codex-anchor-mac/scripts/register_mcp.py
```

The marketplace uses a local relative plugin path, so it installs this fork's
files instead of fetching the upstream repository. If an upstream marketplace
with the same name is already configured, remove that marketplace entry first
with `codex plugin marketplace remove codex-anchor-mac`, then add this checkout.
Restart the desktop app after installation so the MCP process loads the new
code. The daily job remains in `~/.codex-anchor/`, outside the source checkout.
Do not use the standalone `setup.sh` from a TCC-protected Documents directory;
use the plugin's install/repair action instead.

### Upstream installation

In macOS Terminal, after verifying `codex --version`:

```bash
codex plugin marketplace add https://github.com/unnamedhoead/codex-anchor-mac.git
codex plugin add codex-anchor-mac@codex-anchor-mac
python3 ~/.codex/plugins/cache/codex-anchor-mac/codex-anchor-mac/*/scripts/register_mcp.py
```

### Open the panel in a new chat

After registration, open a **new chat** in the Codex desktop app. In the message
box, type `@`, select **Codex Anchor** from the plugin picker, and send:

```text
@Codex Anchor Open the anchor panel.
```

Select the actual plugin mention rather than typing only its name. A chat
that was already open before registration may not have the MCP tools. After
upgrading, run the registration command again and start another new chat;
restart the desktop app if it still shows the old panel.

The `register_mcp.py` command is not optional and not a bug in your setup: Codex installs
the plugin and marks it enabled, but it does not read `mcp.json` and start
the server, so without it the plugin is installed and its tools never appear.
Re-run it after upgrading the plugin — the cache path carries the version, so
the old registration points at a directory that no longer exists.

Installing the plugin installs the daily job: the first time the panel opens
it creates `~/.codex-anchor/`, installs the launchd job, and offers to
schedule the daily wake through the standard macOS authorization dialog. You
can decline that last part — the job still runs, it is just late on nights
the Mac is asleep on battery.

Without Codex, the job works on its own, without a panel:

```bash
git clone https://github.com/unnamedhoead/codex-anchor-mac.git ~/codex-anchor-mac
cd ~/codex-anchor-mac && ./setup.sh
```

Both routes install into `~/.codex-anchor/`, so they share one log and one
schedule.

## Control panel

![Control panel](docs/panel.png)

The panel renders inside Codex, not in a browser. It lets you:

- change the trigger time, in a timezone you pick
- enable or disable the job
- trigger a run manually
- read the last 15 runs
- switch the interface between English and 中文
- repair the job if a plugin upgrade left a stale copy behind

Changing the time keeps the launchd job, `config.json` and the `pmset` wake in
sync, so they cannot drift apart.

It reaches its backend through MCP tool calls rather than HTTP, because Codex
blocks a sandboxed panel from reaching localhost even when the origin is
declared ([openai/codex#45913](https://github.com/openai/codex/issues/45913)).

## Timezones

launchd only understands the system clock. The panel converts to system-local
time before writing the schedule, so `04:30 Asia/Shanghai` becomes whatever
that is on your machine.

The picker lists 58 zones, not all 598. They are collapsed by their
*(January, July)* offset pair, so one entry stands for one real set of rules
including DST — collapsing by current offset instead would put Europe/London
and Africa/Abidjan in the same bucket, and they differ for half the year.
Your Mac's own zone is always in the list.

If the system timezone later changes — you travelled — the panel shows a banner
and one click re-syncs. Avoid scheduling inside your region's DST transition
hour (commonly 01:00–03:00); that wall-clock time may not exist on changeover
day.

## How it works

`anchor.sh` runs one `codex exec` against the Codex CLI bundled in ChatGPT.app
(or on `PATH`), with:

- `-s read-only` — an explicit sandbox, so an unattended job never inherits a
  permissive global config
- `--ephemeral` — no session history
- a 240-second watchdog and a directory lock
- the prompt `回复ok`, with no file dependency
- per-run overrides that disable apps, plugins, memories, shell tools, configured
  MCP servers, and project-document loading, while preserving authentication,
  provider settings, and the selected model/effort
- a separate final-message file: only an exact `ok` reply plus exit 0 logs success;
  diagnostic output alone is insufficient

It logs to `anchor.log`:

```
2026-10-07 05:02:18 +0800  OK  exit=0 22s tz=CST+0800  reply: ok
```

**It must authenticate as a ChatGPT subscription** (`auth_mode = chatgpt` in
`~/.codex/auth.json`). An OpenAI API key is billed separately and will not
start a subscription window at all.

## Development

```bash
./test.sh
```

The checks use stub binaries and temporary fixtures, so no real
request is ever sent and no quota window is opened. They cover `anchor.sh`'s
success, empty-output, failure, watchdog, missing-binary and locking paths, the
timezone conversion, the MCP protocol surface over stdio, and the guards (TCC
refusal, strict-XML plists, sudoers syntax, manifest consistency, and a panel
that references no external origin). CI runs them on `macos-latest`.

## Known limitations

- A powered-off Mac cannot be woken. `pmset` only wakes it from sleep.
- macOS may skip a scheduled wake on low battery.
- Logging out or switching users unloads the LaunchAgent. Locking the screen is
  fine.
- The DarkWake workaround is **not independently verified** beyond the author's
  machine. If your log shows `[machine slept mid-run; anchor landed late]`,
  it did not work for you — please open an issue with the power log.
- Window behaviour is decided server-side by OpenAI and can change. The
  authoritative reset time is `/status` inside Codex.

## Uninstall

```bash
./uninstall.sh
```

## License

MIT. See [LICENSE](LICENSE).
