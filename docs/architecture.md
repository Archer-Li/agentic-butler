# Architecture (independent v0.2 development)

## Goal

Observe lightweight developer workflow signals, make proactive but minimally intrusive suggestions, and perform only exactly approved operations. Adapters should be independent of the decision engine.

## State transitions

`event: unprocessed -> processed`  
`proposal: pending -> approved | rejected | expired`  
`approved -> running -> completed | failed | interrupted`

An event carries an external idempotency key where available. Proposal creation uses a unique `event_id` constraint inside `BEGIN IMMEDIATE` transactions. A worker may crash between the executor and state update: it must **never replay side effects** automatically without reconciliation.

## Architectural boundaries

1. **Event Source Adapter**: validates and minimizes event metadata, assigns a stable idempotency key. The opt-in Codex notify receiver filters one exact workspace and uses native turn IDs; the Claude Code Stop adapter accepts minimized metadata. SCM polling remains future work.
2. **Event Store**: SQLite WAL database; single-host process boundary.
3. **Triage/Planner**: static local templates with optional explicitly enabled, schema-constrained Ollama triage. The model only selects whether to suggest and cannot plan actions or approve work.
4. **Authorization Gate**: time-bound approval of a canonical scope envelope including repo, session, event metadata, exact steps, executor, tools and effect. No dynamic privilege escalation after approval.
5. **Execution Adapter**: safe workflow simulation plus a separate opt-in, tool-free Codex text adapter. Future adapters must enforce allowlisted tools, side-effect classification and a new approval for any enlarged scope.
6. **Feedback**: mute/unmute, state history, durable triage reservations and opt-in batch notifications. Future evaluation should track acceptance, usefulness, interruptions and cost.

## Important trade-offs

- SQLite is well suited to a single-machine runtime; distributed deployments would require a different storage/lease model.
- Persistent event summaries avoid storing logs and source code, reducing exposure and prompt injection risk.
- A preview executor enables integration testing of approval semantics without accidentally modifying repositories.
- A stable event key requires provider semantics; duplicate external IDs should never have conflicting payloads without investigation.
- The current workflow templates are static. Dynamic plans should use typed steps and a policy validator before approval.

## v0.2 boundaries

See [integration setup](integrations.md) and [security review](review-v02.md). Digests protect internal consistency, not malicious same-user database rewrites. Workflow execution stays preview-only; external executor implementations are rejected. Live text tasks use a separate scope, tables, approval/run path and runtime adapter; preview approvals cannot be upgraded.

## Observation worker and approval lifecycle

The foreground worker shares a single consent-validated evaluation pipeline with the CLI, and can emit optional notifications. It has no execution entry point and does not recover another process on startup. Execution remains a separate explicit command. Pending/approved proposals can transition to revoked, clearing authorization. Proposal, approval, rejection, revocation, execution and recovery transitions append audit rows in the same transactions as state changes. See [Codex setup](codex-setup.md).

## Companion presentation and host callbacks (dev3)

A loopback HTTP process projects the authoritative proposal state into a compact strip and expandable review/activity/settings UI. The browser sends the displayed scope digest to the same approval gate; notifications carry no authority. A strict versioned JSON configuration limits projects/tools and privacy/budget preferences. Configured Codex and Claude receivers share minimized ingestion; no global settings are modified. See [companion setup](companion-setup.md) for the API trust boundary, callback identity limits and native delivery behavior.

## Separate live text adapter (dev4)

`agent_jobs` and `agent_audit` hold immutable task scopes and state transitions. Pending -> approved -> running -> completed/failed/cancelled; pending/approved -> revoked. No task is launched by the worker or automatically replayed. Scope freezes prompt, project/workspace, expiry, provider, zero allowed tools, timeout/output limits, config digest and CLI binary digest. Run rechecks policy, expiry, integrity and binary before claiming the task transactionally. Active runs poll revocation/config opt-out, enforce wall-time/output limits and terminate their own child process group/tree. Interrupted running rows remain blocked from rerun; inspect the process before manual reconciliation. See [live acceptance](live-acceptance.md).

## Project memory (v0.3.dev1)

Project UUIDs bind local memory to one normalized workspace. Structured checkpoints form a working inbox; lifecycle signals enqueue a debounced companion pass. Local dreaming groups exact verified-note content and produces a version-bound change draft. Per-note incorporation preserves unprocessed notes. Approval and application are separate actions, with atomic revision promotion and an audit trail. Historical restore also produces a reviewable draft.

Startup hooks and workflow review retrieve a bounded reference bundle. Live text tasks freeze that bundle into their approval scope and recheck referenced guidance before and during execution. Optional semantic consolidation is an ordinary separately approved tool-free text task with frozen source records and note identities. Import validates its output and produces another pending memory review. Neither callbacks nor the worker execute agents or promote memory. See [memory setup](memory-setup.md).
