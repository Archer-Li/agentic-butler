# v0.2 opt-in integrations

All integrations are disabled until explicitly invoked. Installation does not modify agent settings, download models, install a daemon, enable notifications or authorize execution.

## Claude Code Stop input

Implemented from the public [Claude Code hook reference](https://code.claude.com/docs/en/hooks), checked 2026-10-23. Only `Stop` is supported. Required input fields are `hook_event_name`, `session_id`, and boolean `stop_hook_active`. Active stop-hook loops are ignored. No transcript or source file is opened; all other payload fields are discarded. Input is capped at 64 KiB.

Manual example (PowerShell):

```powershell
'{"hook_event_name":"Stop","session_id":"demo-1","stop_hook_active":false}' | pco --db ./demo.sqlite3 hook-claude-stop --repo personal-demo --delivery-id turn-1
pco --db ./demo.sqlite3 evaluate
pco --db ./demo.sqlite3 list --status pending
```

For actual hook wiring, manually register a command for the `Stop` event which invokes the installed `pco` executable with `hook-claude-stop --quiet --repo <personal-label> --delivery-id <stable-delivery-id>`. Use absolute paths for the executable and `--db`; quote paths using the host shell's rules. This project deliberately does not write your agent settings.

Claude's Stop payload has no stable delivery identifier. A caller wrapper must supply a unique ID per turn and preserve it on retry; do not use the session ID alone. The adapter hashes repository label + session + delivery ID for deduplication. Hook success with `--quiet` emits no stdout. Validation failures exit 1, not the Stop-blocking exit 2. It never outputs an agent permission decision. Wiring and native-agent execution are not yet live-tested; the stdin protocol is covered by fixtures and CLI tests.

## Local low-cost LLM triage

The initial reasoning adapter uses an already-installed local Ollama model `qwen2.5:0.5b`. It never installs or pulls a model. Based on the public [Ollama chat API](https://docs.ollama.com/api/chat), checked 2026-10-23.

```text
pco --db ./demo.sqlite3 evaluate --llm-triage --llm-consent --llm-max-calls 10
```

Only an allowlisted event kind is sent to `http://127.0.0.1:11434/api/chat`. Subjects, repository names, session IDs, code and logs are omitted rather than regex-redacted. Environment proxies and redirects are disabled. No tools are exposed to the model. The model can only return an exact `{"suggest": boolean}` object; it cannot supply plans, commands, approval or privileges.

Limits: 10-second network timeout, 16-KiB response cap, 1024-token requested context, 32 generated tokens per request. These are local server generation settings, not a billing guarantee. A SQLite reservation is committed before the request. `--llm-max-calls` is a database-lifetime ceiling, capped at 100; increasing it explicitly raises that ceiling. Failed/abandoned calls consume a reservation and do not auto-retry. Decisions are cached per event. On model failure, budget exhaustion or `false`, events remain unevaluated. A later plain `evaluate` explicitly chooses deterministic evaluation instead. `serve` uses deterministic rules only.

This avoids paid cloud requests; the local service and its model installation remain trusted dependencies. Live inference was not run during development. Transport behavior is tested with local fixtures. Semantic usefulness from event kind alone is intentionally limited and needs future measurement.

## User notifications

```text
pco --db ./demo.sqlite3 notify --consent
pco --db ./demo.sqlite3 notify --consent --desktop
```

Default output is a JSON batch count. `--desktop` additionally uses an optional Tk desktop information dialog. Tk and a graphical session must be available; this is not a native notification-center integration. Desktop delivery is covered by an injected backend in tests, not a visual OS test.

No event text or repository label appears in the dialog. Pending, unexpired proposals are claimed in SQLite before display, once per proposal; batches have a persisted 60-second cooldown. Failed delivery is recorded and not retried automatically. A crash after claim may lose a notice, deliberately preferring silence over duplicate interruption. A dialog cannot approve or launch work. Review `pco list`, then explicitly approve the proposal ID.
