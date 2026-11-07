# Implementation plan — 2026-10-27

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
