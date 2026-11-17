# Approved local commands

Butler can run a configured test command in a selected personal project. Enable this separately from workflow previews and Codex text tasks. Existing configurations load with commands disabled.

## Configure a test command

Add this section to your application JSON. Replace the executable with the absolute path to your project's Python interpreter, such as the Python executable inside its virtual environment. The project name must match an existing configured project.

```json
"commands": {
  "enabled": true,
  "allowlist": [
    {
      "name": "python-tests",
      "project": "personal",
      "argv": ["/absolute/path/to/python", "-m", "unittest", "discover", "-s", "tests", "-v"],
      "timeout_seconds": 120
    }
  ]
}
```

Use forward slashes for Windows executable paths. Up to 32 named commands can be registered. Arguments are passed directly to the executable with shell parsing disabled. Shell scripts, shell launchers and common elevation launchers are rejected. There is no installer or automatic privilege request.

Run `pco config-check pco.json` and restart the companion. In **Local commands**, choose the command and click **Prepare command for review**. A workflow detail also offers matching commands for its project. Preparing a command creates a new pending task.

Review the executable and arguments, resolved working directory, effect, timeout, output limit and expiry. **Approve this command** and **Run approved command** are separate actions. Preview approval does not approve a command. After execution, the card shows combined stdout/stderr, exit code and audit transitions. Cancel is available while running. You can use a verified finding from the result to save a memory checkpoint for separate review.

## CLI

```sh
pco --config pco.json command-prepare --name python-tests
pco --config pco.json command-list
pco --config pco.json command-show <task-id>
pco --config pco.json command-approve <task-id> --expect-scope <reviewed-hash>
pco --config pco.json command-run <task-id> --expect-scope <reviewed-hash>
```

Use `command-revoke <task-id>` before execution or `command-cancel <task-id>` from another terminal while a command runs. Failed or cancelled runs return a nonzero CLI exit status. A new attempt requires a new task and approval. Concurrent runs of the same task have one winner.

## Execution boundary

Tests run project code with the current OS user's permissions. They can modify files, contact services and launch other processes. Register and approve commands only for workspaces and code you trust. The executable's hash and the configuration are checked at approval and launch. The scope binds the resolved workspace and exact arguments. Project source, installed packages and external resources are not frozen by that approval.

The child receives a small set of OS runtime environment variables. API keys and Python startup options from the parent environment are excluded. Tests may still read credentials from files available to the OS user. The output is retained locally and can contain sensitive test data.

The runner limits wall-clock execution to 1–300 seconds and combined captured output to 64 KiB. Cancellation or a policy change requests termination. POSIX uses a process group and Windows uses taskkill for the running process tree. Detached processes and processes whose parent has already exited can escape termination, particularly on Windows. This version does not provide an OS sandbox or a Windows Job Object.

Running tasks are claimed durably before launch and are never automatically replayed. If Butler or the machine exits during execution, a task may remain marked running. Verify and stop any surviving process before preparing a new attempt. Crash reconciliation and stronger process containment remain future work.

## Verification

Automated tests cover the approval gate, tampering, configuration opt-out, executable changes, expiry, revocation, concurrent claims, cancellation, real subprocess output and exit status, timeout and output limits. HTTP tests cover token/origin enforcement and the separate approval/run sequence. UI tests cover literal output rendering and disabled run controls before approval.
