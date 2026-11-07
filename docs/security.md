# Security boundaries and future hardening

This prototype cannot be used to launch unreviewed agent operations. It is a reference workflow engine, not a production-ready automation system.

## Implemented

- Explicit approval before any executor is scheduled
- Scope-envelope digest + approved digest + proposal expiration
- Preview workflows have no shell or repo access; optional consented loopback-only Ollama triage. Separate live text tasks require per-task approval and use the existing Codex login without storing credentials.
- Controlled set of event kinds; short event summaries
- Durable SQLite state with serialized proposal creation
- Fail closed for interrupted operations

## Required before enabling real agents

- Source adapters must not collect secrets, complete prompts, code, proprietary plans or logs by default.
- Untrusted PR descriptions, issue text and logs must be treated as **data**, never authority. Enforce prompt-injection boundaries.
- Approval must specify repo, session, exact actions, allowed tools, effect level and a short TTL. The v0.2 envelope binds repository/session labels, actions, tools, effect and expiry, but labels do not establish authenticated identity or workspace ownership; it remains insufficient for production authorization.
- Execution must be tied to the currently authorized identity, repository and workspace, with hard time/token/budget limits.
- Require a new approval when a subagent requests privileges beyond those originally granted.
- Real adapters need cancellation, timeouts, rate limits, circuit breakers, retries only for idempotent operations and audit logs.
- Define notification frequency policies and user preference retention/deletion before a public beta.
- Add adversarial tests for concurrency, modified proposals, forged hook events, duplicate delivery and crash recovery.

No employer code, secrets, internal configuration or data should be imported into this repository.

## v0.2 review

Read [review-v02.md](review-v02.md) for addressed findings, migration semantics and remaining trust limitations, and [integrations.md](integrations.md) for consent, privacy and budget behavior. Hook success is observation only. Neither model output nor notifications can approve or execute proposals.

## Observation workflow hardening

Codex callbacks use an exact lexical workspace filter and native delivery IDs; foreign workspaces and unsupported event types are ignored. Payloads are capped at 64 KiB of UTF-8. The generator writes a new configuration snippet, never agent settings. Explicit reviewed-scope hashes, pre-start revocation, targeted execution and transactional audit history supplement the existing gate. Callback metadata remains untrusted, and callbacks grant no live execution authority. See [setup and trust boundaries](codex-setup.md).

## Local companion

Only loopback binding is supported. Requests must match Host, Origin and fetch-site restrictions; API access uses a random per-launch token. No CORS, GET mutation, arbitrary static file paths or arbitrary command endpoints are provided. A separate authenticated live-text endpoint uses the same origin/token checks. Browser rendering uses textContent. Unknown configuration fields, unsupported execution settings and invalid consent/budgets fail validation. Native notification programs are fixed, bounded and opt-in; notification actions never authorize work. See [companion-setup.md](companion-setup.md).

## Live text boundary (dev4)

This limited adapter does not satisfy the requirements for general coding/tool execution above. It supplies only explicitly reviewed task text, disables project AGENTS loading (`project_doc_max_bytes=0`), ignores user configuration, disables shell/apps/plugins/hooks/multi-agent/browser/computer/image/skill/memory/code-host capabilities, uses an ephemeral read-only run and no approval bypass. Completion notify is an explicit minimized local callback override. Environment is restricted to OS/runtime/login paths; inherited cloud API keys/endpoints are omitted. CLI login, binary and provider infrastructure remain trusted. CLI compatibility must be checked before supporting another release. A specific known diagnostic saying Code Mode is disabled is accepted; other error/tool item types fail the text contract. Post-run event checks supplement runtime controls; they are not a substitute for tool isolation.

Prompts and text results are persisted in the local database, unlike minimized event hooks. Users must review both the prompt and its disclosure before approving. Prompt bytes, wall time and output bytes are bounded; no token or dollar billing ceiling is claimed. No automatic retries, privilege escalation, deployment, code edits or external executor injection are supported. Scope digests protect consistency, not a malicious same-user rewrite of code/database/config. A process crash may leave a running record requiring manual process inspection; it is never resumed automatically. Native notification submission does not establish physical display.

## Notification review receipts (dev5)

Links are restricted to the current loopback origin and carry an opaque ID in the fragment. A 24-hour open receipt is acknowledged only by same-origin/token-authenticated POST; GET does not change receipt/proposal/execution state. Receipt IDs cannot approve or run tasks. Unknown/expired IDs and foreign origins fail closed. Local timestamps indicate page acknowledgement, not proof of physical OS activation. No notification history or browser history is read. See [notification links](notification-links.md).

Notification availability checks read only the documented Windows API for the sender identity. They do not send a toast, inspect notification history, change settings, or imply display confirmation. New native submission records use submitted rather than delivered; detected OS blocks are durable and do not trigger retries or fallback permissions.

## Project memory (v0.3.dev1)

Memory requires explicit project opt-in. Lifecycle adapters filter the exact workspace before opening state, cap payloads, avoid transcript paths and accept only structured checkpoints. Verification is an attributed claim, not an independent trust elevation. Memory is reference data and grants no execution authority. Host snippets are generated for review and never installed automatically.

Change scopes bind the project, configuration, revision, before/after snapshots, note selection and expiry. Canonical hashes and transactional promotion reject stale or tampered changes and concurrent double application. Pinned changes require explicit consent. Semantic jobs expose bounded source text for separate task approval; unsupported output and references outside that snapshot fail closed. Applying model output still requires memory approval and application. Existing same-origin/token controls protect memory API actions. Stored checkpoints and historical revisions remain local and are not hard-deleted by retirement; local runtime/database/configuration integrity remains a trust assumption.
