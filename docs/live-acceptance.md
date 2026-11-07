# Live integrations — dev4 (2026-10-28)

## Delivered and observed

- Installed Codex CLI: 0.165.0-alpha.1.2, Windows, existing account login. No credentials read or copied.
- Real tool-free completion returned `PCO_LIVE_CALLBACK_OK`; the configured native notify receiver recorded the turn.
- Separate exact-scope live task returned `PCO_APPROVED_LIVE_OK`, with audit `pending -> approved -> running -> completed` and a new callback. Earlier incompatible startup error events were recorded as failed, not success.
- Claude statusLine renderer was invoked through its generated Windows PowerShell shell command, using JSON stdin. It returned a single ANSI purple line with actual SQLite counts, exit 0 and no stderr. This tests the real command/protocol; it does not prove rendering in Claude Code.
- Windows native notification was triggered from the companion's Test native notification button. OS submission succeeded. The user subsequently confirmed seeing the Orchestrator notification. A later fresh retry also passed physical click-through, confirmed by the user and its matching local open receipt. No notification preference or OS policy was changed.

## Opt-in live text setup

Use only a personal workspace and the installed package in its virtual environment. Existing preview scopes stay simulation-only. Add the following section to the local application JSON (the complete example defaults enabled to false):

```json
"agent": {"enabled": true, "provider": "codex", "timeout_seconds": 60}
```

Restart the companion. Live text tasks lets you select an enabled Codex project, enter task text, prepare and inspect the full immutable scope, approve that text task, then separately run it. Revoke prevents a pending/approved launch; Cancel terminates an active child and discards its result. You can also use:

```text
pco --config pco.json agent-prepare --project personal --prompt "Explain this independently authored idea in two sentences."
pco --config pco.json agent-show <id>
pco --config pco.json agent-approve <id> --expect-scope <reviewed-hash>
pco --config pco.json agent-run <id>
pco --config pco.json agent-revoke <another-id>
pco --config pco.json agent-cancel <running-id>
pco --config pco.json agent-list
```

The scope binds prompt, personal workspace, provider, expiry, no allowed tools, configuration digest and Codex binary digest. Changed configuration, executable, prompt or expiry requires a new task and review. Execution checks policy before launch and during the turn. No worker launch/retry, tool commands, file edits, deploy/merge/publish or privileges are exposed. An interrupted running row cannot be run again; inspect whether its process is still active before manual reconciliation.

Task text is sent to OpenAI through the existing Codex login; prompt and final text are stored locally. No source files or automatic project instructions are supplied. Limits: 8,000 UTF-8 prompt bytes, 1–120 seconds wall time, 64 KiB combined stdout/stderr, 16,000 retained result characters. These are not token or monetary billing caps. Code Mode remains disabled; the exact CLI startup diagnostic confirming that fact is tolerated. Unrelated error items and tool/action items fail the text-only contract. CLI/runtime remain trusted; this is not production authorization for general coding agents.

## Native Claude Code status line

Generate the snippet without changing host settings:

```text
# Windows: explicit PowerShell invocation, works even with a Git Bash host shell
pco --config pco.json statusline-config --project personal --shell powershell
# macOS/Linux
pco --config pco.json statusline-config --project personal --shell posix
```

Review and merge its `statusLine` field into the relevant Claude project settings, preserving any existing custom status line. The script validates the exact workspace/tool opt-in before opening SQLite in read-only mode; it prints fixed labels, safe project names, numeric counts and a validated ANSI color. It cannot approve or execute. It reads neither transcripts nor source files. Claude itself chooses when to refresh the line. Our machine has no Claude Code executable, so its native display and Stop firing remain pending rather than simulated as verified.

## Remaining acceptance work

1. **Codex desktop chrome:** no confirmed public API for an arbitrary permanent PCO strip inside native Codex chrome. Current browser companion is an explicitly labeled panel. Do not patch the installed app or claim a browser page is native embedding. Evaluate a supported plugin panel once a suitable host extension point is available.
2. **Claude host:** install/login under the user's control, merge the reviewed project snippets, observe a real Stop event and native status-line update. No automatic installation or credential collection.
3. **Physical notifications:** Windows display and clicking back into the loopback review queue passed a controlled fresh retry with user confirmation and a matching receipt. Verify macOS on a physical Mac. Submission success and OS CI are insufficient. Fullscreen/focus/OS identity policies can suppress display; do not disable those policies automatically.
4. **General coding execution:** design separately reviewed per-tool/file scopes, stronger isolation, billing controls and recovery before enabling modifications. Text-only approval never upgrades to file/tool access.

References checked 2026-10-28: [Claude status lines](https://code.claude.com/docs/en/statusline), [Claude hooks](https://code.claude.com/docs/en/hooks), [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference), [supported plugin extensions](https://developers.openai.com/plugins/build/extensions). The native Codex placement limitation is our assessment of the documented extension points, not a claim that all future host versions will lack one.

## dev5 notification continuation

Review deep links, local authenticated open receipts and browser expansion/navigation handling are implemented and tested. These are distinct from physical OS click-through. See [notification acceptance](notification-links.md) for current evidence and manual test instructions.

## dev6 notification reliability follow-up

The user did not see the subsequent native click-test notice. Its old delivered status meant API submission only; no review open was recorded. Windows currently reports Enabled, which does not explain or disprove the missed display. The first seen notice remains valid evidence for that one test, but reliable native display and OS click-through are not accepted. New native states distinguish submitted from blocked, with a read-only notification-check command. See [follow-up evidence](notification-links.md).

## Fresh Windows retry accepted — 2026-10-29

The requested resend was seen, and the user confirmed clicking into the review list. The matching native receipt was acknowledged about five seconds after creation; no proposal was approved and no execution was created. Windows display/click-through passes this controlled test. The previous missed notice and its undetermined cause remain in the record; macOS and native host placement remain pending. See [retry evidence](notification-links.md).
