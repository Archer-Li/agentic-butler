# Roadmap

This is a proposal, not a commitment to implement all features at once.

## v0.1 — safe control plane (included)

- [x] Event inbox with idempotency keys and normalized stdin adapter
- [x] Persistent state and proposal transitions
- [x] Approval / rejection / expiry / mute
- [x] Preview-only execution and ledger
- [x] Minimal integration-friendly CLI and tests

## v0.2 — first useful local integration

- [x] Implement Claude Code Stop and Codex completed-turn input adapters from public docs (live delivery pending)
- [x] Add opt-in desktop information dialog and batch JSON notifications (native notification center pending)
- [ ] Replace static templates with schema-validated workflow proposal objects
- [x] Add optional small local Ollama triage with metadata omission, consent and durable call ceiling (live inference pending)
- [ ] Per-repo and per-session policy filters (scope binding and caller delivery IDs implemented)
- [ ] Metrics: proposal precision, acceptance rate, feedback and latency

## v0.3 — bounded real execution

- [ ] Read-only coding-agent execution behind explicit scope-bound approval
- [ ] Skills registry using explicit allowlisting and capability descriptions
- [ ] Cancellable task execution with persisted checkpoints
- [ ] Deadline and retry policy; no at-least-once side effects
- [ ] Threat-model-driven integration tests

## Later

- [x] Implement the first project memory loop with local dreaming, checkpoint evidence, lifecycle adapters, versioned review, transactional apply and restores
- [x] Support optional semantic drafts through separately approved text tasks
- [ ] Verify memory lifecycle hook firing in both live hosts and add richer semantic relevance/retention policies

- [ ] Additional agent backends and source adapters
- [ ] More robust cross-session preference models and privacy controls
- [ ] Hierarchical delegation with bounded depth, spend and permissions
- [ ] Review-based expansion of side-effect capabilities

Implemented foundation: canonical scope envelope, approval digest and built-in preview executor allowlist. Static templates remain; dynamic workflow generation and live execution are deferred.

Dev2 adds Codex setup generation, exact callback workspace filtering, explicit reviewed-scope approval, revocation, transactional audit history, targeted previews, foreground notification/triage integration and local diagnostics. Per-repo policy files, semantic metrics and live execution remain open.

Dev3 implements a loopback companion UI, compact colored strip, real proposal review/actions/audit, strict application configuration, dual configured receivers and optional native/browser notification adapters. Windows browser interaction and callback subprocesses are verified; native OS display, live host events, host plugin embedding and installers remain pending. CI now includes Windows/macOS/Linux.
