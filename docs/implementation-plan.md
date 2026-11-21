# Implementation plan — 2026-10-27

## Current priorities — 2026-11-16

P0 is an evidence-based CI failure investigation that adapts to work already in progress.

1. Import an explicitly selected failed CI signal, bounded failure log and selected project files into a local evidence snapshot. Display exactly what a model would receive.
2. Prepare a read-only diagnosis task under the existing per-task approval and run controls. Return causes, cited evidence, uncertainty and validation steps.
3. Let the user approve a separate task that produces a reviewable, file-specific modification plan from the diagnosis.
4. Recheck selected files on observation, review and execution. Mark old recommendations stale when the user or another agent changes them. Changed evidence requires a new task and approval.
5. Support an explicit “I have made the fix” signal that reduces the recommendation to verification. A passing, separately approved verification command bound to the current selected files closes the investigation and collapses its details. Edits alone never establish that the issue is fixed.
6. Exercise a complete synthetic failure, diagnosis, human edit and successful verification. Preserve auditable evidence and describe the scope of verification.

The initial implementation uses imported CI logs or a failed local command's output. Automatic GitHub log collection, richer agent activity coordination and whole-repository change detection are follow-ups. Only selected files are observed. Polling cannot infer a person's intent or prove that a change is a complete fix.

P1 follows this workflow with a 30–50-task pilot evaluation dataset. Capture memory snapshots available before each task and label relevant evidence. Compare no memory, the current keyword method, BM25 and hybrid BM25/embeddings under equal model/context budgets. Measure Recall@K, irrelevant and stale context, tokens, retrieval latency and independently judged task outcomes. Separate tuning tasks from held-out evaluation. Dataset collection uses independently authored personal material and records provenance. No semantic retrieval improvement is claimed before evaluation.

Acceptance for dynamic collaboration includes changing a selected file midway through diagnosis, refusing stale approvals, preparing an updated task from current evidence, reducing a user-reported fix to verification, and collapsing the suggestion after a matching check passes. Observing state never dispatches a model or command.

Goal: turn the existing approval engine into an inspectable, local companion for Codex and Claude Code. Keep all executions preview-only until a real executor has an explicitly approved authorization policy.

1. Add a strict, versioned application configuration with safe defaults, tool/project allowlists, colors, notification preferences, TTL and local-model budget. Provide init/validate commands and an example.
2. Build a responsive compact colored status strip and expandable review/activity/settings views, backed by real SQLite state. Provide separate explicit approval, revoke/reject and preview-run actions. Show disconnected/stale observations without pretending to have a live agent connection.
3. Serve the companion on loopback only, with per-launch CSRF protection, same-origin checks, no CORS, bounded input, no arbitrary commands and no automatic execution. Make it usable in the Codex browser panel and ordinary browsers on Windows/macOS. This is a local companion panel; native Codex chrome injection remains unconfirmed.
4. Provide configuration-driven Codex and Claude Code event receivers. Filter exact opted-in workspaces before recording minimized metadata. Generate reviewable hook setup snippets without modifying global host settings. Deduplicate native Codex turn IDs and minimized Claude Stop fingerprints without reading transcripts.
5. Add opt-in native notification adapters for Windows/macOS and keep portable dialog fallback. Notifications open the review workflow, never approve or run it. Respect cooldown and quiet hours; record delivery failures.
6. Update README, setup guide and capability matrix, explicitly distinguishing implemented/tested behavior from live-host, native notification and Mac verification still pending. Extend CI to Windows/macOS/Linux and test real callback/HTTP/approval flows. Build/install, visually inspect the companion, commit/push to the existing private repository, and inspect CI.

Acceptance: the user can open a real local colored strip, inspect incoming suggestions from either supported receiver, approve the exact scope, revoke it, preview an approved workflow, and inspect its audit trail. Misconfigured projects, stale approvals, cross-origin requests and unapproved execution fail closed. No employer resource is used and no public release or collaborator invitation occurs.

Host-specific limits are not silently substituted: an ordinary browser panel is not a native Codex status-bar extension. Real agent execution, signed installers, host plugin registration and physical Mac notification verification remain separate acceptance items if unavailable in this environment.

## Execution result

Implemented in dev3: items 1–4 for the local companion surface; native/browser/dialog delivery adapters, cooldown/quiet hours; README/example settings/setup guide; HTTP security and dual-receiver subprocess tests; Windows browser review/approval/revoke interaction. Windows/macOS/Linux CI is configured. Signed installers, native Codex chrome embedding, real agent execution and physical native-notification verification remain outstanding acceptance items. The exact requested native information-bar placement is not claimed complete.

## dev4 continuation — 2026-10-28

Implemented separate scope-bound live Codex text jobs and UI/CLI controls; real approved tool-free turn and completion callback verified on Windows. Added native Claude statusLine renderer/generator; its actual Windows shell/stdin protocol passed. Added explicit native-notification test UI and submitted a real Windows toast; the user confirmed seeing the Windows notification. Click-through and physical macOS display remain unverified. Native Codex desktop chrome insertion has no confirmed public extension point. Claude host executable and a physical Mac are unavailable here; those host/display checks remain pending. General file-modifying coding agent execution remains gated out. See [evidence and next acceptance work](live-acceptance.md).

## Windows notification retry — 2026-10-29

A fresh notice was resent at the user's request. The user confirmed display and clicking into review; its unique receipt was acknowledged and the database showed no approved proposals or executions. Windows display/click-through passes the controlled test. The earlier missed notice is retained as an unexplained observation. macOS/Claude host/native Codex placement are separate pending work.
