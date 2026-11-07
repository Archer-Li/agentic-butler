# Codex observation and approval workflow (v0.2.dev2)

This integration observes completed turns in one explicitly selected personal workspace. It does not start Codex, execute a prompt, change its sandbox or approve any agent tool request.

## Install and generate configuration

Install this package in a Python 3.10+ virtual environment. Use the installed `pco` command from that environment so the generated Python interpreter can import the receiver.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/pco.exe --db ./personal.sqlite3 init
.venv/Scripts/pco.exe --db ./personal.sqlite3 codex-config --repo personal-project --workspace . --output ./codex-notify.toml
```

The new file contains a single TOML `notify` entry using an argument array and absolute interpreter, database and workspace paths. Existing output files are never overwritten. No shell expression, execution-policy bypass or privilege flag is generated.

Review and manually merge that **root-level** entry into your user-level Codex configuration (`~/.codex/config.toml`). Do not put it under an existing TOML table. If you already use a notify program, review how to combine the receivers; do not blindly append a duplicate entry. Codex ignores `notify` in project-local configuration. This tool never edits your Codex settings or authorization policy.

Official references: [notification callback](https://learn.chatgpt.com/docs/config-file/config-advanced#notifications) and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), checked 2026-10-23. The documented notification program receives one JSON argument containing event type, thread ID and turn ID. Compatibility was checked against locally installed `codex-cli 0.163.0-alpha.11.2` help; a real agent turn has not been launched for this integration.

## Observe events and review proposals

Run in a foreground terminal:

```powershell
pco --db ./personal.sqlite3 serve --notify --notification-consent
# Optional desktop information dialog:
pco --db ./personal.sqlite3 serve --notify --notification-consent --desktop
```

For a single pass use `serve --once`. The worker only evaluates events and notifies; it never calls the execution function. To opt into local-model triage, add `--llm-triage --llm-consent --llm-max-calls 10`. Consent and budgets use the same validation as one-shot `evaluate`. The result reports persisted triage outcomes. No model is downloaded or installed.

In another terminal, inspect the pending proposal and its scope digest:

```text
pco --db ./personal.sqlite3 list --status pending
pco --db ./personal.sqlite3 show <proposal-id>
pco --db ./personal.sqlite3 approve <proposal-id> --expect-scope <scope-hash-from-show>
pco --db ./personal.sqlite3 run --id <proposal-id>
```

The final command is **simulation only**. Without approval it returns `nothing_approved`. `--expect-scope` prevents approval when the displayed digest does not match; it supplements the integrity checks already performed during approval and execution.

Withdraw work before it starts with `pco --db ./personal.sqlite3 revoke <proposal-id>`. Revocation clears its approved digest and is terminal. It cannot cancel work already marked running. Rejection and revocation are available even for legacy or corrupt scope envelopes. `show` includes ordered audit entries and computed integrity, expiry and execution eligibility. Records from older versions do not get fabricated historical entries.

Use `pco --db ./personal.sqlite3 doctor` for local capability checks. Finding a CLI/module does not mean its service or GUI works. The report explicitly lists live checks it did not perform.

## Privacy and boundaries

- Only `agent-turn-complete` from the exact configured `cwd` is accepted. Other workspaces are ignored without opening a database. Comparison is lexical and platform-normalized; subdirectories and symlink aliases are not implicitly included. Missing `cwd` or delivery IDs fail closed.
- Only the selected repository label, thread identifier, a fixed summary and a hashed delivery key are stored. User prompts, assistant replies, cwd, transcripts and code are discarded. Codex itself passes the raw callback JSON in argv, so local process inspection may expose it before this adapter discards it.
- The receiver emits no stdout, decisions, instructions or permission grants. Errors contain only a fixed diagnostic, with nonzero exit status. Input is capped at 64 KiB of UTF-8; host command-line limits may be lower.
- The callback is not authenticated. A local caller can forge metadata and create a suggestion, never approve or execute work.
- Approval audit rows are append-only through the application, not cryptographically protected against the same OS user editing SQLite.
- The worker no longer marks running jobs interrupted on startup, which could interfere with another process. Recovery is an explicit manual `recover` operation after verifying no worker is executing that job.
- Approval TTL is bounded to 1–120 minutes. Existing legacy data is retained; no old approval is upgraded into a new capability.

## Validation status

Covered: executable callback subprocess, generated TOML (including Unicode and paths with spaces), malformed/oversized inputs, workspace isolation, deduplication, scope review, revocation, targeted preview execution, transactional audit order and observation-only worker behavior.

Not yet live-verified: Codex invoking the callback after a real turn, local Ollama inference, desktop display. Real agent execution, bounded cancellation and authenticated workspace identity remain future work; no live executor is included.
