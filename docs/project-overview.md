# Agentic Butler

![Agentic Butler. Project memory. Human approval.](assets/readme-banner.png)

Agentic Butler is a **local companion for coding workflows**. It brings signals and suggested actions into one review queue, adapts when work is already in progress, and carries lessons you approve into later coding-agent sessions.

**Open source · v0.3.0a1 Alpha · Python 3.10+** · [Download](https://github.com/Archer-Li/agentic-butler/releases/tag/v0.3.0a1) · [Quick start](../README.md#quick-start)

## Five-slide walkthrough

Expand each slide to follow an example from the review queue to a verified fix.

<details open>
<summary><strong>01 · Meet your coding companion</strong></summary>

### See what needs your attention

Butler accepts configured Codex and Claude Code callbacks and manual signals. The local panel brings suggestions, approved commands, and proposed memory updates together so you can decide what happens next.

![The Butler review queue showing a sample CI failure and distinct approval and preview controls](assets/companion-review.jpg)

The screenshot shows a built-in workflow preview. Configured test commands and Codex text tasks have their own approval and run controls.

</details>

<details>
<summary><strong>02 · Turn a CI failure into a reviewable plan</strong></summary>

### Start with evidence you can check

Select the failure, import a log or choose captured local command output, and select up to four small project files. Before running the diagnosis, review the exact evidence that the task will send to OpenAI through your Codex login.

```mermaid
flowchart TD
    Signal["CI failure signal"] --> Evidence["Select the log and relevant files"]
    Evidence --> Approval["Approve and run the diagnosis"]
    Approval --> Findings["Review findings, cited lines, and uncertainty"]
    Findings --> PlanApproval["Approve and run a separate plan task"]
    PlanApproval --> Plan["Review proposed edits and validation steps"]
    classDef gate fill:#b51235,stroke:#d71941,color:#fffaf0
    class Approval,PlanApproval gate
```

**Example** — A test expected `add(2, 2)` to return `4` but got `0`. Butler can cite the failure log and the line that subtracts the arguments, then propose changing that line and rerunning the focused test. The plan stays available for human review. [CI investigation guide](ci-investigations.md)

</details>

<details>
<summary><strong>03 · Let the investigation catch up with you</strong></summary>

### Keep the next useful step in view

Butler rechecks the contents of the files you selected. A change makes the old recommendation stale and blocks reuse of the old approval. If you report the fix, the investigation shifts to verification.

```mermaid
flowchart TD
    Edit["A selected file changes"] --> Stale["Mark the earlier advice stale"]
    Stale --> Progress{"Where are you in the fix?"}
    Progress -->|Still investigating| Refresh["Prepare a fresh review from current evidence"]
    Progress -->|Fix reported| Verify["Approve and run the configured check"]
    Verify -->|Passes| Fold["Collapse to a compact result"]
    Verify -->|Fails| Retry["Keep verification open and review the output"]
    classDef gate fill:#b51235,stroke:#d71941,color:#fffaf0
    class Progress gate
```

A pass applies to the selected local check and its evidence snapshot. Remote CI status is checked separately.

</details>

<details>
<summary><strong>04 · Save a lesson for the next session</strong></summary>

### Carry forward what the project learned

A checkpoint records a finding, where it applies, and the evidence behind it. Review the proposed memory update before applying it. Later sessions can retrieve relevant guidance, and revisions let you update or restore it.

```mermaid
flowchart LR
    Checkpoint["Finding and evidence"] --> Draft["Proposed memory update"]
    Draft --> Review["Human review"]
    Review --> Revision["Apply a versioned lesson"]
    Revision --> Session["Reuse in a later session"]
    Session -.-> Checkpoint
    classDef gate fill:#b51235,stroke:#d71941,color:#fffaf0
    class Review gate
```

For example, keep the Windows-specific cause of a failing test alongside the focused check that confirmed it. [Project memory setup](memory-setup.md)

</details>

<details>
<summary><strong>05 · Try the Alpha on one project</strong></summary>

### Start with one workspace and one check

1. Install the Alpha wheel in a Python virtual environment.
2. Run `pco config-init pco.json`, configure a project you trust, and validate it with `pco config-check pco.json`.
3. Run `pco --config pco.json ui --open`. Follow the CI guide to opt in and register a verification command.

The Alpha accepts imported logs and captured local command output. Observation covers selected files. Diagnosis, plan generation, and local command execution each require separate approval.

[Download v0.3.0a1](https://github.com/Archer-Li/agentic-butler/releases/tag/v0.3.0a1) · [Companion setup](companion-setup.md) · [Security and execution boundaries](security.md)

</details>
