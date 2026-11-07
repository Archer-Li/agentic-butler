# Project memory setup — v0.3.dev1

Project memory joins Wingman's checkpoint, draft, review and task-context flow. Local dreaming groups exact matching verified findings and merges their evidence. Optional semantic drafting uses a separately approved Codex text task, followed by a separately reviewed memory diff.

## Enable a personal project

The complete pco.example.json template includes memory with enabled false. Existing version-1 configurations migrate with memory disabled. Add the following section to the application JSON, naming only configured personal projects.

```json
"memory": {
  "enabled": true,
  "projects": ["personal"],
  "bundle_bytes": 4096,
  "max_records": 64,
  "max_observations": 1000,
  "debounce_seconds": 30
}
```

Start the installed companion with `pco --config pco.json ui --open`. Experience updates shows the approved revision, working checkpoints and proposed changes. Memory opt-out is checked against the saved configuration for memory API writes, worker drafting and memory visibility. Changing the database requires a companion restart. Other legacy application preferences retain their documented restart behavior.

A stable local project ID is bound to its normalized workspace. Renaming a configured label for that same workspace preserves memory identity. Moving an existing label to a different workspace requires an explicit mapping migration outside this first release. Each configured project currently has one exact workspace. Additional worktree mappings remain future work.

## Capture and publish a lesson

The UI provides a checkpoint form with session, task/attempt ID, finding, applicability and verification evidence. Reuse the task/attempt ID for multiple reports about the same attempt. Verified is an agent/user claim whose explanation is visible during review. Unverified notes remain available as working handoff context. Checkpoints use unique delivery IDs, and a conflicting retry fails.

For CLI use, save this independently authored example as checkpoint.json encoded in UTF-8.

```json
{
  "notes": [{
    "kind": "lesson",
    "text": "Use explicit UTF-8 when reading this project's UTF-8 text on Windows.",
    "conditions": "Windows scripts reading project text",
    "verified": true,
    "verification": "The focused decoding fixture passed after the encoding change.",
    "incident_id": "encoding-fix-attempt-1"
  }]
}
```

Feed its contents into the command's stdin using your shell's UTF-8 input support.

```text
pco --config pco.json memory-record --project personal --session session-1 --delivery-id checkpoint-1 --final
pco --config pco.json memory-dream --project personal
pco --config pco.json memory-review --project personal <draft-id>
pco --config pco.json memory-approve --project personal <draft-id> --expect-scope <reviewed-hash>
pco --config pco.json memory-apply --project personal <draft-id> --expect-scope <reviewed-hash>
pco --config pco.json memory-read --project personal --query "Windows text encoding"
```

`--final` durably schedules a local draft after the debounce window. The companion worker prepares drafts and leaves them pending for review. Plain checkpoints stay available for a later lifecycle completion signal or an explicit dream command. The foreground companion must be running for background drafting.

Review shows before/after records, applicability, source checkpoints, verification claims and audit history. Approval freezes the complete diff, evidence selection, base revision, expiry and configuration digest. Apply creates the next revision and incorporates the selected checkpoint notes in one transaction. A changed base/configuration or altered/expired scope requires fresh review. Concurrent apply has one winner. Mixed checkpoints retain their unincorporated notes.

Use memory-reject to dismiss a draft's selected notes, memory-revoke to withdraw its approval/draft while retaining notes, and memory-dismiss to discard a working checkpoint after withdrawing any draft that uses it. The UI provides these actions. Memory-rollback prepares a new reviewed change that restores a chosen revision. History remains available. Pinning or altering pinned guidance requires explicit allow-pinned review. Retiring a record removes it from active retrieval while retaining prior revisions.

## Semantic dreaming

Enable the existing agent text adapter only when you want its provider-backed draft. Its reviewed prompt contains selected project records and verified notes, sent to OpenAI through the existing Codex login.

```text
pco --config pco.json memory-model-prepare --project personal
pco --config pco.json agent-show <task-id>
pco --config pco.json agent-approve <task-id> --expect-scope <reviewed-task-hash>
pco --config pco.json agent-run <task-id>
pco --config pco.json memory-model-import --project personal --agent-id <task-id>
```

Then inspect, approve and apply the resulting memory change through the normal memory review flow. The UI exposes Prepare semantic draft task, the existing Live text tasks approval/run controls, and Prepare memory review for a completed semantic task.

The adapter freezes its source revision, records and note identities. Import rejects changed inputs, out-of-snapshot record/evidence references and unsupported output. The model selects explicit note indexes to incorporate. Pinned edits require the manual reviewed path. The worker waits while an eligible semantic task is awaiting completion/import. Manual local drafting remains available. There is no automatic model dispatch or retry. Each provider call uses the existing per-task approval and runtime limits. Token/dollar billing caps remain provider-specific work.

## Host lifecycle hooks

Generate a fresh snippet with the installed virtual-environment Python.

```text
pco --config pco.json memory-hook-config --project personal --tool codex --output codex-memory-hooks.json
pco --config pco.json memory-hook-config --project personal --tool claude --output claude-memory-hooks.json
```

Review and merge the generated hooks into the chosen host configuration while preserving existing handlers. The generator leaves host settings untouched and reports installed false. Codex uses a quoted POSIX command and commandWindows override. Claude uses its documented exec-form command/args. Check support in the installed host version before merging.

SessionStart/SubagentStart supply a bounded memory bundle and checkpoint instructions, including an argv/stdin example when it fits the protocol hint limit. Stop/SubagentStop/PreCompact/SessionEnd persist metadata and queue bookkeeping. A Stop/SubagentStop answer containing exactly a pco_checkpoint object with a notes array can contribute a structured checkpoint. Arbitrary final prose is discarded after fingerprinting for delivery deduplication. Existing completed-turn receivers also enqueue pending learning when memory is opted in.

Startup prefers the current session's working checkpoints and falls back to recent unfinished project checkpoints. Lifecycle output uses the host's hook-specific JSON shape. Recursive stop-hook calls are ignored. End handlers use a short local operation and leave heavy processing to the companion worker. Payloads are capped at 64 KiB and filter exact workspace/tool opt-in before opening state. Transcript paths and source files stay unopened. A collection error emits a fixed diagnostic and advisory success JSON, allowing the host session to continue. A later checkpoint can be retried with the original stable delivery ID.

Public sources checked 2026-11-06. [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Claude hooks](https://code.claude.com/docs/en/hooks).

## Scope, limits and current acceptance

Approved records and a bounded, attributed working handoff can enter a task's displayed approval scope. Changing or retiring a referenced record invalidates that context, and active tasks poll for the change. A new unrelated record is available to later tasks. Retrieval uses simple lexical relevance and a byte budget. Oversized pinned content is surfaced for correction in the memory panel.

Each note has a 1500-byte text limit, a checkpoint has at most 10 notes and 8000 serialized bytes, and a local dream scans up to 100 checkpoints with at most 32 distinct changes per draft. Context defaults to 4096 bytes. The startup command/schema hint has a separate 1500-byte cap. Revision review has one pending/approved change per project. SQLite transactions serialize local dreaming and promotion. Model drafting is a separate bounded job. Explicit per-project opt-in, canonical scope hashes and same-origin/token-protected API actions enforce the workflow.

This release retains historical revisions and observation payloads locally. User-requested hard forgetting, automatic retention deletion, semantic vector retrieval, cross-project personal preference memory and additional worktree mapping remain future work. Agent/user verification claims require review against their evidence. The local user, configuration, runtime and database remain trusted. Generic employer material is outside the allowed input scope for this personal project.

Tests cover project isolation, schema migration, retry deduplication, mixed-note consumption, pinned/capacity controls, stale/tampered scope rejection, interrupted and concurrent apply, rollback, lifecycle JSON adapters, worker drafting, HTTP authorization, semantic input/output validation and UI behavior. Generated host protocols are tested separately from actual lifecycle firing in each installed client. Live Claude lifecycle acceptance and physical Mac testing remain pending.

Windows acceptance on 2026-11-06 used an isolated synthetic project: the actual companion UI separately approved and applied a draft consolidating two checkpoints into revision 1. A real tool-free Codex task then returned the marker supplied only by that approved memory. A separately approved Codex semantic task produced a valid pending memory draft while revision 1 remained unchanged. An earlier malformed model response was rejected without promotion; the output contract now includes an explicit action-field example. Both generated hook argument vectors also ran as real subprocesses with startup context and idempotent subagent-stop delivery. No host settings were installed or modified during these checks.
