# Product experience: a visible, proactive workflow companion

User product direction recorded 2026-10-23. This is the target experience, not a claim that these UI surfaces have shipped. The CLI and SQLite engine are supporting infrastructure.

## Primary experience

The assistant stays visible as a compact, single-line colored information bar inside the user's coding tool, starting with Codex. It communicates whether anything needs attention, whether help is suggested, and whether a workflow awaits the user's decision. The normal state should be quiet and unobtrusive.

Example content, not a screenshot of an implemented extension:

```text
● Orchestrator · 2 things need attention · 1 workflow ready   [Review]
```

Use a stable product accent color for identity. State indicators also have words and icons so color is not the only signal:

| State | Indicator | Interaction |
| --- | --- | --- |
| Quiet | Neutral / green, Nothing needs attention | Open recent activity |
| Attention | Amber, Needs attention | Inspect the reason and supporting signal |
| Proposal | Violet, Workflow ready for review | Open the proposed plan |
| Waiting | Amber, Your decision is needed | Review, approve once, defer, or dismiss |
| Running | Blue, Approved work in progress | Inspect progress; cancellation only when implemented |
| Problem | Red, Work failed or was interrupted | Inspect outcome and recovery options |
| Disconnected | Gray, Tool connection unavailable | Show reconnection guidance; never imply a healthy connection |

Attention priority and permission state are separate. An urgent signal is not authorization. A completed agent turn is a signal for evaluation, not automatically an urgent problem. Freshness, tool identity, project/session scope and disconnected status must be visible in detail views.

## Notification and decision flow

1. An enabled tool adapter observes an allowed event in an opted-in personal project.
2. Triage determines whether a useful suggestion is warranted; ordinary signals should not cause repeated interruptions.
3. The bar updates, and an opted-in OS notification explains briefly what needs attention or where help may be useful.
4. Clicking the notification opens the relevant proposal, without granting permission or starting work.
5. The proposal explains why it matters, what the planned workflow will do, the target project/session, exact actions, tools, side effects, expected resource limits and approval expiry.
6. The user chooses review/approve once, defer, dismiss, or mute. Approval binds the reviewed plan and scope. Any expansion requires a new decision.
7. When a real executor is implemented, a distinct explicit run action starts only that authorized workflow. Status, completion and failures return to the bar and activity view.

Notification actions must never launch an agent directly. Defer and snooze need explicit persisted semantics before being exposed as working buttons. Do not show a working Run action while only the preview executor exists. Current builds must label their result as a simulation.

## Platform and tool support

Windows and macOS are both first-class targets. Both require native OS notification delivery, click-through to proposal details, consistent decisions, accessible controls, quiet hours, deduplication and notification preferences. The existing Tk information dialog is a prototype backend, not completion of this requirement. Neither platform is considered supported based on Python portability alone; each needs installation and interaction tests on the real OS.

Users may work with Codex, Claude Code or other tools. Event capture, embedded UI, notifications and execution are independent capabilities. A tool supporting an event hook does not imply it supports embedded UI or execution. Unsupported capabilities must be clearly labeled; do not weaken approvals to make adapters look compatible.

Architecture:

```text
Tool event adapters -> minimized events -> triage/plans -> approval authority
                                                 |              |
                                     shared presentation state   |
                                      /          |          \    |
                          embedded bar/panel   activity   OS notices
                                                                |
                                           explicit authorized executor
```

The shared presentation state should project authoritative backend state without creating permissions. Host integrations render that state; OS backends deliver notification intents. Per-user settings choose enabled tools, personal projects, notification levels and mute preferences. No employer resources, prompts or code are inputs to development or test fixtures.

## Codex surface feasibility

Official documentation confirms MCP Apps UI alongside conversations and conversation-panel/sidebar entry points:

- [Add UI to an MCP server](https://developers.openai.com/plugins/build/chatgpt-ui)
- [Plugin extensions](https://developers.openai.com/plugins/build/extensions)

Checked 2026-10-23. These documents do not establish an arbitrary persistent colored strip in the Codex desktop chrome. That exact placement remains a feasibility item, not an implemented capability. A compact conversation panel is a documented candidate. An independent companion window would be a fallback requiring an explicit product decision; it is not equivalent to the requested in-Codex bar. Terminal status-line settings are also not proof of desktop UI extensibility.

## Delivery sequence and acceptance

1. Build a working compact UI prototype against the existing proposal state. Show quiet, attention, awaiting-decision, disconnected and simulation states. Review the placement and interaction before treating a panel as the final bar.
2. Connect review, approval, revoke/dismiss and activity views to the shared authority. Validate stale scopes, expired plans and notification clicks without execution.
3. Implement native Windows and macOS notifications behind the same interface, with platform-specific verification. Keep opt-in, rate limits and privacy defaults.
4. Package and validate a Codex host integration using supported surfaces, then add equivalent tool adapters with a published capability matrix.
5. Add bounded real execution only after the execution authorization policy is explicitly settled and enforced. Verify the complete approved workflow and cancellation/recovery on supported platforms.

Current dev2 provides event ingestion, proposals, scope-bound preview approval, revoke/audit, a foreground worker and callback configuration. It does **not** yet provide the bar, embedded panel, native notification-center integration, multi-platform installers or real agent execution. Those are product work still to do.
