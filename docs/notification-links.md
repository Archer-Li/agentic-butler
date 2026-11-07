# Notification review links — dev5

## Behavior and local verification

Native notification links now point to `/review#notice=<random-id>` rather than the compact homepage. The review queue expands on initial navigation; later polling preserves the user's chosen tab and collapse state. Browser notification clicks focus the companion, expand review and acknowledge the same kind of link.

A link has a random ID, pinned loopback origin, backend, creation time, 24-hour expiry and nullable opened time in local SQLite. The fragment stays out of HTTP request paths. The ID authorizes no workflow action. Loading a page with GET records no open. Only an authenticated, same-origin POST can acknowledge a known, unexpired ID; repeated opens preserve the first timestamp. Forged, expired, foreign-origin and malformed receipts fail closed. No external redirect target is accepted. The UI shows review-open feedback in Tools & settings.

An open receipt means the corresponding review page acknowledged the link. It is not proof of physical OS display or of an OS click rather than direct navigation. Windows display has separately been confirmed by the user. On a fresh retry the user confirmed seeing the Windows notification and clicking into the review queue, and its matching local open receipt was recorded. This confirms the controlled Windows click-through test. macOS physical display/click behavior remains unverified. The built-in macOS osascript fallback and Tk dialog do not support link activation; terminal-notifier does when already installed.

Manual browser inspection confirmed `/review` directly shows an expanded queue with an awaiting-review proposal. Unit/HTTP tests cover authenticated acknowledgement, expiry, idempotence, foreign origins, external target rejection and the invariant that no proposal is approved and no execution is created. UI regressions cover expansion, acknowledgement once, preserved navigation and receipt errors without falsely disconnecting the companion.

## Use

Keep the foreground companion running. Enable native notifications in your local application configuration and restart, then use Test native notification in Tools & settings. Click its body in the Windows banner or notification center. The browser should open the expanded review list; the page confirms review opened and Tools & settings shows the first open time. Do not select Approve or Run as part of this notification check. A stopped server or changed random port can make old notices unreachable; a stable --port is optional and does not install a background service.

Only link IDs and timestamps are retained; no system notification history, browser history, transcripts or source files are collected. Configuration remains opt-in, and neither model output nor notification/open receipts can approve, execute or enlarge task scope.

## Native host placement

The official [MCP quickstart](https://developers.openai.com/plugins/build/app-quickstart) documents MCP tools for ChatGPT/Codex and optional UI in ChatGPT. [Plugin extensions](https://developers.openai.com/plugins/build/extensions) documents ChatGPT sidebar and conversation panels. These establish supported plugin surfaces, but do not establish an API for an arbitrary permanent strip in native Codex desktop chrome. No installed app is patched and no public tunnel or plugin deployment is created. Claude Code native statusLine remains implemented/protocol-tested; the Claude executable and a physical Mac are unavailable in this environment.

Diagnostics also report the actual configured live-text opt-in instead of a fixed disabled value, and show whether the Claude CLI is installed. These checks do not launch a host, infer a model response, or prove OS notification display. Preview execution and separate live-task approval remain distinct.

## Reported missed notice — 2026-10-29

The user reported not seeing the native click-test notice. Its historical SQLite row has status `delivered`, which in dev5 represented API submission, not visible delivery; its review-open time remains null. Windows ToastNotifier.Setting returned Enabled in the same normal-user context as the sender. This does not establish why the banner was missed; no focus/OS policy cause is asserted. A previous notice was seen, so Windows display is observed once but not accepted as reliable. At that point OS click-through remained unverified; the fresh retry below subsequently passed.

Dev6 records new native submissions as `submitted` and records detected app/user/policy blocks as `blocked`. Historical rows are preserved. The Windows sender checks the documented ToastNotifier.Setting before Show. Unsupported/unknown responses fail closed, and blocked notices are not automatically retried. The test button reports a block instead of success; that feedback survives UI polling. The read-only `pco notification-check` reports availability with display_verified false, sends no toast and does not open the state database. It reports unavailable when the runtime cannot query WinRT. No notification/security/focus setting is modified, and no notification history is read.

Source: [Microsoft ToastNotifier.Setting](https://learn.microsoft.com/en-us/uwp/api/windows.ui.notifications.toastnotifier.setting?view=winrt-26100). The existing PowerShell app identity is a prototype delivery mechanism shared with PowerShell; a dedicated packaged identity and physical display/click testing remain future work.

## Fresh Windows retry passed — 2026-10-29

At the user's explicit request, one fresh native test notice was sent after confirming that the local review page was reachable. The sender returned submitted with os_setting Enabled. The user confirmed seeing it and clicking into the review list. Its unique native link was created at 2026-10-29T04:26:02.448700Z and acknowledged at 2026-10-29T04:26:07.215454Z. No automated test navigated to that receipt URL. The test database still had zero executions and no approved proposal after the click.

Windows physical display and click-through into review therefore pass this controlled acceptance test. The earlier missed notice remains documented; its cause (including the user's possible-expiry explanation) is not established. This is not a guarantee that all future banners will appear. macOS physical display/click-through and dedicated packaged Windows identity remain separate work.
