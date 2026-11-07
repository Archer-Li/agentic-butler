# Agentic Butler

![Agentic Butler. Project memory. Human approval.](docs/assets/readme-banner.png)

Agentic Butler is a local workflow companion for **Codex and Claude Code**. It gives you one place to review workflow suggestions and project memory, so you can decide what runs and what the next session should remember.

Save work checkpoints, review proposed updates to project memory, and carry approved guidance into the next session. An optional Codex text task can draft a memory update for separate review. See [memory setup](docs/memory-setup.md) and the [design](docs/project-memory-design.md).

**Developer preview (v0.3.dev1).** Workflow execution is preview-only. Explicitly approved Codex text tasks are available as an opt-in. This is an independently developed personal project, licensed under the [MIT License](LICENSE).

[Companion screenshot from the first developer preview](docs/assets/companion.jpg)

## What you can use now

- A compact colored strip that expands into a real review queue, workflow details, activity/audit history and tool/settings views.
- Shared event/proposal state for Codex and Claude Code, with strict personal-workspace/tool opt-in, minimized callbacks and duplicate suppression.
- Explicit scope-bound preview approval, dismissal, revocation and separate preview execution. A notification can never authorize work.
- A versioned JSON configuration for appearance, project/tool selection, notification preferences, quiet hours, approval expiry and local-model limits.
- Optional browser/system notifications, Windows/macOS native delivery adapters and a Tk dialog fallback. Windows display and click-through to the expanded review queue were confirmed on a fresh retry, with a matching local open receipt. One earlier notice was missed; macOS display/click-through remains unverified.
- A foreground worker, durable SQLite state, failure-safe recovery, diagnostics and tests.
- Separate live Codex text tasks with exact-scope approval, revocation, cancellation, timeout/output limits and a native completion callback. No tool access is granted.
- A read-only colored Claude Code native status-line adapter and opt-in settings generator.

## Quick start

Python 3.10+ with no third-party runtime dependencies. Install in a virtual environment from this checkout:

```sh
python -m pip install -e .
pco config-init pco.json
```

Edit `pco.json`: add a personal project label, its absolute workspace path, and `"tools": ["codex", "claude"]`. Empty project lists opt in nothing. Then:

```sh
pco config-check pco.json
pco --config pco.json ui --open
```

The local URL printed by the server can also be opened in the Codex browser panel. The terminal must stay running. Expand the strip to inspect proposals. Approve preview and Run preview are separate actions; revocation prevents later execution.

For platform-specific installation, hook setup, notification permissions and configuration semantics, read **[Companion setup](docs/companion-setup.md)**. Start from [pco.example.json](pco.example.json). Settings are validated, not arbitrary executable commands. The live text adapter requires both configuration opt-in and separate per-task approval; existing preview approvals never authorize it.

## Connect your tool

```sh
pco --config pco.json host-config --project my-personal-project --tool codex --output codex-notify.toml
pco --config pco.json host-config --project my-personal-project --tool claude --output claude-hooks.json
```

Review the generated snippets before merging into host settings; the tool never changes global Codex/Claude settings or installs a daemon. Codex uses completed-turn notifications. Claude uses Stop exec-form hooks. Both feed the same review/approval workflow. They do not launch another agent.

## Support and verification

| Capability | Current status |
| --- | --- |
| Local colored strip, expandable review UI | Implemented; Windows browser interaction verified |
| Codex and Claude configured callback receivers | Implemented; real subprocess delivery with synthetic payloads tested |
| Live Codex completion callback | Verified on Windows with Codex 0.165.0-alpha.1.2 and an actual tool-free turn; live Claude Stop remains unverified |
| Claude Code native status line | Implemented; Windows shell/stdin protocol verified; host binary unavailable for in-host display test |
| Windows/macOS/Linux Python support | CI matrix configured; check the current [Actions results](https://github.com/Archer-Li/agentic-butler/actions) |
| Browser notification click-through | Implemented; requires an opted-in configuration and browser permission |
| Native Windows/macOS notifications | Windows display and OS click-through confirmed on retry with matching receipt; earlier missed notice retained in evidence; macOS display/click-through unverified |
| Native Codex desktop status-bar injection | Not implemented; no confirmed public insertion API |
| Real agent execution / cancellation | Tool-free Codex text tasks implemented; actual approved turn + callback verified; arbitrary coding/tool execution remains unavailable |
| Signed standalone desktop installers | Not implemented |

The companion panel is not presented as native Codex chrome. The product goal remains an unobtrusive in-tool assistant; [product direction](docs/product-experience.md) and the [implementation plan](docs/implementation-plan.md) track that distinction. Claude's initial Stop fingerprint may merge identical final answers within a session; see the setup guide.

## CLI and tests

The original CLI remains available for manual events and integrations:

```sh
pco --db ./scratch.sqlite3 emit ci_failed "Synthetic personal-project CI signal" --repo personal --event-key demo-1
pco --db ./scratch.sqlite3 evaluate
pco --db ./scratch.sqlite3 list --status pending
pco --db ./scratch.sqlite3 show <proposal-id>
pco --db ./scratch.sqlite3 approve <proposal-id> --expect-scope <reviewed-hash>
pco --db ./scratch.sqlite3 run --id <proposal-id>
pco --db ./scratch.sqlite3 revoke <another-proposal-id>
python -m unittest discover -s tests -v
node tests/web_state.test.cjs  # optional local UI state checks; included in CI
```

`emit-json`, `serve`, `mute`, `unmute`, `recover` and `doctor` remain available. Manual legacy CLI commands use explicit caller-supplied state and are separate from the companion's project allowlist. Only recover a running record after verifying that no process is working on it.

## Safety and architecture

Events -> minimized inbox -> triage/static plans -> proposal -> human approval -> separate preview -> audit.

The preview executor exposes no shell, repository source access, cloud model endpoint, privileged action or real agent launch. A separate opt-in live text adapter invokes the installed Codex CLI under read-only restrictions, disables tools and automatic project instructions, and sends the explicitly reviewed task text to OpenAI using existing login. Optional local Ollama triage sends only an allowlisted event kind, requires consent and reserves a durable call budget. No model or notification helper is installed automatically. Loopback API mutations require a launch token and same-origin checks. Same-user database/code tampering remains outside the trust boundary.

Read [architecture](docs/architecture.md), [security](docs/security.md), [review findings](docs/review-v02.md), [integration limitations](docs/integrations.md) and [roadmap](docs/roadmap.md).

## Live text tasks and native status line (dev4)

See [live acceptance and setup](docs/live-acceptance.md) for commands, exact limits, observed evidence and pending host/display checks. Live text task prompts/results are stored locally; they must contain only independently authored personal material. No source files are supplied. Time/output caps are not token or monetary billing caps. The default configuration keeps live tasks and notifications disabled.

## Notification review links (dev5)

Notifications target `/review` and expand the review queue even in compact mode. Authenticated, same-origin open receipts record only a local review-open timestamp; opening a notification never approves or runs a task. Browser rendering of the route and receipt/security regressions are verified separately from actual OS click-through. See [notification acceptance](docs/notification-links.md).

## Notification reliability follow-up (dev6)

Native submission is labeled `submitted`, never proof of display. Windows app/user/policy blocks are detected and reported without changing OS settings. `pco notification-check` reads OS availability without sending a notification or opening the state database. `Enabled` still does not prove a visible banner. See [notification acceptance](docs/notification-links.md) for the reported missed notice and remaining checks.

## Project memory (v0.3.dev1)

Opt selected personal projects into the memory section of your configuration. Experience updates in the companion lets you save checkpoints, inspect evidence and before/after changes, approve and separately apply revisions, revise/archive guidance and prepare restores. The worker creates local drafts from verified checkpoint notes. Reviewed project context is frozen into live task approval. Lifecycle hook snippets provide startup context and durable end-of-work signals for Codex and Claude Code. Optional semantic tasks use the existing live-task approval controls and return to a separate memory review.

The implementation uses exact-content grouping for local dreaming. Verification flags represent agent/user claims shown with their evidence. Semantic drafting requires live-agent opt-in and task approval. Host lifecycle delivery must be verified on the installed client version. [Setup, commands and limits](docs/memory-setup.md).
