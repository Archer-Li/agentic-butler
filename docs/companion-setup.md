# Companion setup — v0.2.dev3

The companion is a real local interface backed by the existing SQLite approval engine. It starts as a compact colored strip; expand it to review suggestions, inspect exact actions and expiry, approve a preview, revoke/dismiss, run a preview, and inspect audit history. Tools/settings and activity views are included. It is a browser companion panel, not an extension injected into Codex desktop chrome.

## Install (Windows / macOS)

Python 3.10+ is required. Create a virtual environment, then install from this private checkout:

```powershell
# Windows PowerShell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/pco.exe config-init pco.json
```

```sh
# macOS
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/pco config-init pco.json
```

Edit `pco.json` and add only your independently authored personal workspaces. Use an absolute path appropriate to the OS:

```json
"projects": [
  {"name": "my-personal-project", "workspace": "/absolute/personal/project", "tools": ["codex", "claude"]}
]
```

On Windows use forward slashes (`C:/Users/.../my-project`) or escaped backslashes in JSON. The database path is relative to the configuration file, not the terminal's current directory.

```text
pco config-check pco.json
pco --config pco.json ui --open
```

The printed `http://127.0.0.1:<port>` address can also be opened in the Codex browser panel. Keep the terminal process running. Ctrl-C stops it. No service, startup task, host plugin, OS permission or global tool configuration is installed. The default port is chosen by the OS; use `--port 8765` for a stable local URL.

## Connect Codex and Claude Code

Generate reviewable host settings with the **installed** `pco` from the same virtual environment:

```text
pco --config pco.json host-config --project my-personal-project --tool codex --output codex-notify.toml
pco --config pco.json host-config --project my-personal-project --tool claude --output claude-hooks.json
```

These files are new snippets, never auto-installed or overwritten. Review and merge only the relevant entry into your host's settings. Preserve any existing hooks/notify programs. Codex's `notify` setting belongs at the root of user configuration or an explicit invocation override, not project-local configuration. Claude's snippet uses a Stop command hook with `command` + `args` exec form; use a Claude Code release supporting exec-form hooks and `onFailure` (documented requirement: v2.1.295+ for `onFailure`). No shell quoting or privilege flags are involved. Host settings location and availability can vary by installed release.

Sources checked 2026-10-27: [Codex notification configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#notifications), [Claude hooks](https://code.claude.com/docs/en/hooks).

Both receivers filter the exact configured workspace and enabled tool before opening the database. Codex deduplicates native turn IDs. Claude Stop lacks a native delivery ID: the adapter fingerprints the final assistant answer in memory after workspace filtering, then hashes the fingerprint into the event key. The answer itself, raw payload and transcript paths are never stored or opened. Repeated identical answers in the same session coalesce, potentially losing a repeated signal; this favors avoiding noise. Stop events without the required final answer fail closed. The earlier manual `hook-claude-stop` command remains available for caller-provided delivery IDs.

## Application configuration

`pco.example.json` is a complete safe template. Unknown sections/keys fail validation; there is no arbitrary command, cloud endpoint or credential field.

| Section | Supported settings |
| --- | --- |
| `appearance` | Six-digit `accent` color; `compact` initial strip mode |
| `projects` | Unique project labels, absolute workspaces, enabled `codex`/`claude` tools |
| `approval` | 1–120 minute TTL; execution must remain `preview_only` |
| `agent` | Disabled by default; Codex text-only provider; timeout 1–120 seconds; per-task approval still required |
| `triage` | Local Ollama opt-in, explicit consent, lifetime database call ceiling 1–100 |
| `notifications` | Opt-in, `browser`/`native`/`dialog`, 30–86400 second cooldown, local quiet hours |

Changes take effect on restart; the UI shows settings but does not edit the file. Empty project lists record and show no project through the configured receivers/companion. Legacy CLI commands remain explicit manual tools and do not inherit these project restrictions unless stated; the companion and configured receivers enforce their own allowlist.

Notifications are disabled by default. For browser notifications, set `enabled` to true and `backend` to `browser`, restart, then click **Enable browser notifications** in Tools & settings and approve the browser permission. Click-through focuses the review panel; it never grants approval. Claims/cooldown persist in SQLite so reloading does not automatically replay notices.

For native delivery set `backend` to `native`. Windows uses the system WinRT toast API via a fixed PowerShell program without execution-policy bypass. Toast clicks request the local review URL. macOS uses an already-installed `terminal-notifier` if present (click-through supported); otherwise built-in `osascript` delivers a notification without click-through. No helper is downloaded. OS identity/notification permission policy can prevent display; invocation failure is recorded and not auto-retried. Successful submission does not prove the OS displayed a toast. Use browser delivery when native integration is unavailable. `dialog` is an optional Tk popup fallback.

Quiet hours are `["22:00", "08:00"]` in the machine's local timezone; crossing midnight is supported. Equal start/end means all-day quiet. The cooldown and claims prefer no duplicate interruption over automatic retry after a crash.

## Security and limits

The server binds only to loopback. Mutations require a per-launch token, a valid Host header and same-origin requests. There is no CORS, public listening address, GET mutation or arbitrary command-execution endpoint. UI text uses `textContent`, avoiding interpretation of event text as HTML. The local OS account, application files and SQLite remain trusted; tokens/digests are not protection from a malicious process running as the same user.

Opening the panel or clicking a notification does not approve a workflow. The UI sends the reviewed digest on approval and uses the existing state machine. Run preview is separate and still simulation-only; it never launches a real coding agent. The separate Live text tasks tab is an opt-in adapter with its own approval scope; see [setup and limits](live-acceptance.md). Revoked, expired or altered proposals cannot execute. A recent callback is labeled an observation, never a proven live connection. A failed native delivery appears in SQLite diagnostics rather than exposing exception text.

The static UI is portable and CI covers Windows/macOS/Linux Python behavior. Live Codex callbacks have been verified with a tool-free turn. Actual Claude host firing, browser permission support, macOS notifications, dedicated Windows app identity, signed installers, and native host status-bar embedding require separate live acceptance. Do not interpret mocked OS notification tests or an OS CI runner as physical notification-center validation.

## Tool opt-out during review

On restart, disabled tools are excluded not only from new callbacks but also from proposal evaluation, UI visibility, approval/preview actions and notification claims. Existing records and audit history are retained. Manual events in enabled projects remain available. Muted signals are consumed without model calls and do not replay on unmute. The original explicit manual CLI remains a separate interface, as described above.

## Native status line and live text tasks

The new statusline-config generator and separate live-task commands are documented in [live acceptance](live-acceptance.md). Existing version-1 configurations missing the agent section migrate in memory with live tasks disabled. No host settings are rewritten. Tools & settings includes an explicit Test native notification action when native delivery is opted in; its result says submitted, never display verified.

## Notification review links (dev5)

Native notices now open an expanded `/review` queue. Same-origin, authenticated open receipts record when the review link is acknowledged, without authorizing any work. Browser notifications use the same review-opening behavior. See [link behavior and acceptance](notification-links.md) for limits, expiry and physical OS validation.

Windows native display and clicking into the expanded review queue passed a fresh user-confirmed test with a matching receipt on 2026-10-29. One earlier notice was missed; see [the full evidence](notification-links.md). This does not change notification permissions or imply guaranteed banner display.
