#!/bin/bash
# Fire one tiny Codex request to anchor the start of a 5-hour usage window.
# Designed to run unattended from launchd at ~04:58 so that the window
# boundaries land inside the working day instead of after it.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/anchor.log"
LOCK="$DIR/.anchor.lock"   # a directory: mkdir is the atomic test-and-set
TIMEOUT_SECS="${ANCHOR_TIMEOUT_SECS:-240}"
LABEL="io.github.codex-anchor-mac"
# Overridable so the tests can observe these without touching the real display.
PMSET="${PMSET:-/usr/bin/pmset}"
CAFFEINATE="${CAFFEINATE:-/usr/bin/caffeinate}"

# Codex ships several ways on macOS: bundled inside ChatGPT.app, as a
# standalone app, or installed by npm/brew onto PATH. Try each in turn.
find_codex() {
  [ -n "${CODEX_BIN:-}" ] && { printf '%s' "$CODEX_BIN"; return; }
  local c
  for c in \
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex" \
    "/Applications/Codex.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex" \
    "/Applications/Codex.app/Contents/MacOS/codex" \
    "/opt/homebrew/bin/codex" \
    "/usr/local/bin/codex" \
    "$HOME/.local/bin/codex"
  do
    [ -x "$c" ] && { printf '%s' "$c"; return; }
  done
  command -v codex 2>/dev/null || true
}
CODEX="$(find_codex)"

export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*" >>"$LOG"; }

# launchd appends to this file forever, so a failure from weeks ago keeps
# looking like the current state. Reset it; this run owns its contents.
: > "$DIR/launchd.err.log" 2>/dev/null || true

# Keep the log from growing without bound.
if [ -f "$LOG" ] && [ "$(wc -l <"$LOG")" -gt 2000 ]; then
  tail -n 1000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

# Never let a manual run and the scheduled run overlap.
if ! mkdir "$LOCK" 2>/dev/null; then
  log "SKIP  another anchor run is already in progress"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

if [ -z "$CODEX" ] || [ ! -x "$CODEX" ]; then
  log "FAIL  codex binary not found (set CODEX_BIN to override)"
  exit 1
fi

# Two things can silently desynchronise between runs, and both would otherwise
# only show up as a schedule that quietly fires at the wrong time.
self_check() {
  local cfg="$DIR/config.json" plist="$HOME/Library/LaunchAgents/$LABEL.plist"
  [ -f "$cfg" ] || return 0
  /usr/bin/python3 - "$cfg" "$plist" <<'PYCHK'
import json, os, plistlib, sys

cfg_path, plist_path = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(cfg_path, encoding="utf-8"))
except Exception as exc:
    print(f"config.json unreadable ({exc})")
    raise SystemExit(0)

try:
    link = os.readlink("/etc/localtime").split("zoneinfo/", 1)
    system_tz = link[1] if len(link) == 2 else "UTC"
except OSError:
    system_tz = "UTC"

computed_under = cfg.get("computedUnderSystemTimezone")
if computed_under and computed_under != system_tz:
    print(f"system timezone is {system_tz} but the schedule was computed for "
          f"{computed_under}; re-save it in the panel")

try:
    with open(plist_path, "rb") as fh:
        cal = plistlib.load(fh).get("StartCalendarInterval") or {}
except Exception:
    raise SystemExit(0)

# When the chosen zone IS the system zone, the plist should hold exactly the
# configured time. Any other pairing is a conversion we cannot re-verify here.
if cfg.get("timezone") == system_tz:
    want = (cfg.get("hour"), cfg.get("minute"))
    got = (cal.get("Hour"), cal.get("Minute"))
    if None not in want and want != got:
        print(f"config says {want[0]:02d}:{want[1]:02d} but launchd is set to "
              f"{got[0]:02d}:{got[1]:02d}; re-save it in the panel")
PYCHK
}

while IFS= read -r issue; do
  [ -n "$issue" ] && log "WARN  $issue"
done < <(self_check 2>/dev/null)

EXPECTED_REPLY='ok'
PROMPT='回复ok'

# Read persisted options as separate arguments; never evaluate config as shell.
# Missing settings preserve the original default-model + low behavior.
if ! EXEC_ARGS_TEXT="$(/usr/bin/python3 - "$DIR/config.json" "$CODEX" <<'PYEXEC'
import json, os, pathlib, subprocess, sys
path = pathlib.Path(sys.argv[1])
try:
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    model = cfg.get("model", "")
    effort = cfg.get("reasoning_effort", "low")
    if not isinstance(model, str) or len(model) > 200 or model.strip().startswith("-") or any(
            ord(c) < 32 or ord(c) == 127 for c in model):
        raise ValueError("invalid model ID")
    if effort not in ("", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
        raise ValueError("invalid reasoning effort")
    if model.strip():
        print("--model")
        print(model.strip())
    if effort:
        print("-c")
        print("model_reasoning_effort=" + json.dumps(effort))
    # Ask the CLI to parse effective configuration, including quoted keys and
    # inline tables. Listing servers does not start a model request.
    discovery = subprocess.run([sys.argv[2], "mcp", "list", "--json"],
                               cwd=str(path.parent), capture_output=True, text=True, timeout=30)
    if discovery.returncode:
        raise ValueError("cannot read effective MCP configuration")
    servers = json.loads(discovery.stdout)
    if not isinstance(servers, list) or any(
            not isinstance(s, dict) or not isinstance(s.get("name"), str) for s in servers):
        raise ValueError("invalid MCP configuration response")
    entries = []
    for server in sorted(servers, key=lambda item: item["name"]):
        # Preserve transport kind: CLI overrides merge with existing fields,
        # so replacing an HTTP transport with a dummy command is invalid.
        transport = server.get("transport", {})
        if not isinstance(transport, dict):
            raise ValueError("invalid MCP transport")
        kind = transport.get("type")
        key = {"stdio": "command", "streamable_http": "url"}.get(kind)
        if key is None or not isinstance(transport.get(key), str) or not transport[key]:
            raise ValueError("unsupported MCP transport")
        entries.append(json.dumps(server["name"], ensure_ascii=False) +
                       "={enabled=false," + key + "=" +
                       json.dumps(transport[key], ensure_ascii=False) + "}")
    if entries:
        # A whole table also supports server names containing literal dots.
        print("-c")
        print("mcp_servers={" + ",".join(entries) + "}")
    # Disable local/system skill catalogues only for this invocation. Walk
    # symlinked skill folders too, with a visited set to avoid directory cycles.
    codex_home = pathlib.Path(os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex")))
    roots = [codex_home / "skills", pathlib.Path("/etc/codex/skills")]
    roots += [parent / ".agents" / "skills" for parent in [path.parent, *path.parent.parents]]
    roots.append(pathlib.Path.home() / ".agents" / "skills")
    visited, skill_paths = set(), set()
    for root in roots:
        for folder, dirs, files in os.walk(root, followlinks=True):
            resolved = pathlib.Path(folder).resolve()
            if resolved in visited:
                dirs[:] = []
                continue
            visited.add(resolved)
            if "SKILL.md" in files:
                skill_paths.add(str(resolved / "SKILL.md"))
    print("-c")
    print("skills.config=[" + ",".join(
        "{path=" + json.dumps(p, ensure_ascii=False) + ",enabled=false}"
        for p in sorted(skill_paths)) + "]")
    # Generated beside the job so upgrades only need to copy anchor.sh.
    instructions = path.parent / ".request-instructions.md"
    instructions.write_text("Reply exactly ok.\n", encoding="utf-8")
    print("-c")
    print("model_instructions_file=" + json.dumps(str(instructions), ensure_ascii=False))

except (OSError, ValueError, AttributeError, subprocess.SubprocessError) as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)
PYEXEC
)"; then
  log "FAIL  invalid execution settings in config.json"
  exit 1
fi
EXEC_ARGS=()
while IFS= read -r arg; do
  [ -n "$arg" ] && EXEC_ARGS+=("$arg")
done <<<"$EXEC_ARGS_TEXT"

OUT="$(mktemp -t codex-anchor)"
FINAL="$(mktemp -t codex-anchor-reply)"
START_EPOCH=$(date +%s)

# A pmset wake lands the Mac in DarkWake, where it honours "Sleep Service Back
# to Sleep" over an idle assertion and freezes us mid-request for hours. -u is
# the only flag that forces a real wake (-s is AC-only, see caffeinate(1)), so
# declare user activity to escape DarkWake, then blank the display again right
# away -- nobody wants a laptop lighting up at 04:30.
# ...but only when nobody is at the keyboard. Forcing a wake flashes the
# display, and this script also runs from the test suite, which would strobe
# the screen of whoever is working on it.
if ! "$PMSET" -g assertions 2>/dev/null | grep -qE '^[[:space:]]*UserIsActive[[:space:]]+1'; then
  "$CAFFEINATE" -u -t 1 2>/dev/null
  "$PMSET" displaysleepnow 2>/dev/null
fi

"$CAFFEINATE" -i -s "$CODEX" exec \
  --ephemeral \
  --skip-git-repo-check \
  -C "$DIR" \
  -s read-only \
  -c approval_policy="never" \
  -c notify="[]" \
  -c project_doc_max_bytes=0 \
  -c features.apps=false \
  -c features.plugins=false \
  -c features.memories=false \
  -c features.shell_tool=false \
  -c 'developer_instructions=""' \
  -c features.multi_agent=false \
  -c features.skill_search=false \
  -c features.code_mode_host=false \
  -c features.sleep_tool=false \
  -c features.view_image=false \
  -c features.image_generation=false \
  -c 'web_search="disabled"' \
  --output-last-message "$FINAL" \
  ${EXEC_ARGS[@]+"${EXEC_ARGS[@]}"} \
  "$PROMPT" </dev/null >"$OUT" 2>&1 &
CODEX_PID=$!

( sleep "$TIMEOUT_SECS"
  kill -TERM "$CODEX_PID" 2>/dev/null && sleep 10 && kill -KILL "$CODEX_PID" 2>/dev/null
) >/dev/null 2>&1 &
WATCHER_PID=$!

RC=0
wait "$CODEX_PID" || RC=$?
kill -TERM "$WATCHER_PID" 2>/dev/null
wait "$WATCHER_PID" 2>/dev/null || true

ELAPSED=$(( $(date +%s) - START_EPOCH ))

REPLY="$(cat "$FINAL")"
if [ "$RC" -eq 0 ] && [ "$REPLY" = "$EXPECTED_REPLY" ]; then
  # Only the final model acknowledgement counts, never CLI diagnostics.
  # The watchdog counts awake seconds (correct for detecting a hung request),
  # but ELAPSED is wall-clock, so the two diverge when the Mac slept mid-run.
  SLEPT=""
  [ "$ELAPSED" -gt 120 ] && SLEPT=" [machine slept mid-run; anchor landed late]"
  log "OK    exit=0 ${ELAPSED}s tz=$(date +%Z%z)${SLEPT}  reply: ${REPLY}"
elif [ "$RC" -eq 0 ]; then
  log "WARN  exit=0 but missing or unexpected final acknowledgement ${ELAPSED}s"
  sed 's/^/          | /' "$OUT" | tail -n 20 >>"$LOG"
  RC=1
else
  log "FAIL  exit=$RC ${ELAPSED}s"
  sed 's/^/          | /' "$OUT" | tail -n 20 >>"$LOG"
fi

rm -f "$OUT" "$FINAL"
exit "$RC"
