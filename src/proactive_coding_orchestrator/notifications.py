"""Explicitly opted-in OS notices. A notification never authorizes work."""
from __future__ import annotations

from datetime import datetime, timedelta
import html
import json
import re
import secrets
import shutil
import subprocess
import sys

from urllib.parse import urlsplit

from .core import timestamp, utc_now
from .settings import event_enabled

WINDOWS_SETTINGS = {'Enabled', 'DisabledForApplication', 'DisabledForUser',
                    'DisabledByGroupPolicy', 'DisabledByManifest'}
WINDOWS_NOTIFIER = """$ErrorActionPreference='Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
$notifier=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.Windows.PowerShell')
$setting=$notifier.Setting.ToString()
"""


class NotificationBlocked(ValueError):
    def __init__(self, setting):
        super().__init__('Windows notifications are blocked: ' + setting)
        self.setting = setting


def _windows_call(script, xml=None):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
        input=xml, text=True, capture_output=True, timeout=10, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get('os_setting') not in WINDOWS_SETTINGS:
        raise ValueError('Windows returned an unknown notification setting')
    return value


def native_diagnostics():
    """Read notification availability only; never send or change settings."""
    if sys.platform != 'win32':
        return {'platform': sys.platform, 'os_setting': 'not_checked', 'display_verified': False}
    try:
        value = _windows_call(WINDOWS_NOTIFIER + "@{os_setting=$setting} | ConvertTo-Json -Compress")
        return {'platform': 'win32', 'os_setting': value['os_setting'], 'display_verified': False}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'platform': 'win32', 'os_setting': 'unavailable', 'display_verified': False}


def initialize_links(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS notification_links (
        id TEXT PRIMARY KEY, origin TEXT NOT NULL, backend TEXT NOT NULL,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, opened_at TEXT)''')
    conn.commit()


def review_link(conn, origin, backend):
    parts = urlsplit(origin)
    if (parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port
            or parts.username or parts.password or parts.path or parts.query or parts.fragment):
        raise ValueError('notification target must be the loopback companion origin')
    if backend not in ('native', 'browser', 'dialog'):
        raise ValueError('unsupported notification link backend')
    initialize_links(conn)
    key = secrets.token_urlsafe(24)
    with conn:
        conn.execute('INSERT INTO notification_links VALUES (?,?,?,?,?,NULL)',
                     (key, origin, backend, timestamp(), timestamp(utc_now() + timedelta(hours=24))))
    # Fragment stays out of HTTP access logs and carries no execution authority.
    return {'notification_id': key, 'url': origin + '/review#notice=' + key}


def record_open(conn, origin, key):
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32}', key):
        raise ValueError('invalid notification link')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_links'").fetchone() is None:
        raise ValueError('notification link is unknown or expired')
    with conn:
        row = conn.execute('SELECT * FROM notification_links WHERE id=? AND origin=?', (key, origin)).fetchone()
        if row is None or datetime.fromisoformat(row['expires_at']) <= utc_now():
            raise ValueError('notification link is unknown or expired')
        changed = conn.execute('UPDATE notification_links SET opened_at=? WHERE id=? AND opened_at IS NULL',
                               (timestamp(), key)).rowcount
    return {'status': 'review_opened' if changed else 'already_opened', 'notification_id': key}


def link_status(conn, origin):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_links'").fetchone() is None:
        return None
    row = conn.execute('SELECT created_at,opened_at FROM notification_links WHERE origin=? ORDER BY created_at DESC LIMIT 1',
                       (origin,)).fetchone()
    return dict(row) if row else None


def quiet_now(hours, now=None):
    if hours is None:
        return False
    clock = (now or datetime.now()).strftime("%H:%M")
    start, end = hours
    return start <= clock < end if start < end else (clock >= start or clock < end)


def native_notice(count: int, url: str) -> dict:
    message = f"{count} workflow proposal(s) need your review. Approval is required."
    if sys.platform == "win32":
        xml = ('<toast activationType="protocol" launch="' + html.escape(url, quote=True)
            + '"><visual><binding template="ToastGeneric"><text>Orchestrator</text><text>'
            + message + '</text></binding></visual></toast>')
        # Fixed program; XML arrives on stdin, never interpolated into shell code.
        script = WINDOWS_NOTIFIER + """
if ($setting -ne 'Enabled') {
    @{status='blocked';os_setting=$setting} | ConvertTo-Json -Compress
    exit 0
}
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$doc=New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml([Console]::In.ReadToEnd())
$toast=[Windows.UI.Notifications.ToastNotification]::new($doc)
$notifier.Show($toast)
@{status='submitted';os_setting=$setting} | ConvertTo-Json -Compress
"""
        result = _windows_call(script, xml)
        if result.get('status') == 'blocked':
            raise NotificationBlocked(result['os_setting'])
        if result.get('status') != 'submitted' or result['os_setting'] != 'Enabled':
            raise ValueError('Windows did not confirm notification submission')
        return {**result, 'display_verified': False}
    elif sys.platform == "darwin":
        notifier = shutil.which("terminal-notifier")
        if notifier:
            subprocess.run([notifier, "-title", "Orchestrator", "-message", message, "-open", url],
                           capture_output=True, timeout=10, check=True)
        else:
            # Built-in fallback delivers a notice but cannot deep-link on click.
            script = 'on run argv\ndisplay notification (item 1 of argv) with title "Orchestrator"\nend run'
            subprocess.run(["/usr/bin/osascript", "-e", script, message], capture_output=True, timeout=10, check=True)
    else:
        raise ValueError("native delivery supports Windows/macOS; use browser notifications elsewhere")
    return {'status': 'submitted', 'display_verified': False}


def deliver_batch(conn, config: dict, url: str, *, browser=False, backend=None) -> dict:
    notices = config["notifications"]
    if not notices["enabled"]:
        return {"count": 0, "status": "disabled"}
    if quiet_now(notices["quiet_hours"]):
        return {"count": 0, "status": "quiet_hours"}
    if browser != (notices["backend"] == "browser"):
        raise ValueError("notification backend mismatch")
    conn.execute("BEGIN IMMEDIATE")
    try:
        last = conn.execute("SELECT MAX(at) FROM notifications").fetchone()[0]
        if last:
            from .core import utc_now
            if (utc_now() - datetime.fromisoformat(last)).total_seconds() < notices["cooldown_seconds"]:
                conn.commit()
                return {"count": 0, "status": "cooldown"}
        rows = conn.execute("""SELECT p.id,e.repo,e.source FROM proposals p JOIN events e ON e.id=p.event_id
            WHERE p.status='pending' AND p.expires_at>? AND p.id NOT IN (SELECT proposal_id FROM notifications)""",
                            (timestamp(),)).fetchall()
        ids = [r[0] for r in rows if event_enabled(config, r)]
        conn.executemany("INSERT INTO notifications VALUES (?, ?, 'claimed')", [(key, timestamp()) for key in ids])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if not ids:
        return {"count": 0, "status": "empty"}
    link = review_link(conn, url, notices['backend'])
    status = "claimed" if browser else ("submitted" if notices['backend'] == 'native' else "delivered")
    details = {}
    try:
        if backend:
            backend(len(ids), link['url'])
        elif not browser:
            if notices["backend"] == "dialog":
                from .integrations import desktop_notice
                desktop_notice(len(ids))
            else:
                result = native_notice(len(ids), link['url'])
                if isinstance(result, dict):
                    details = result
    except NotificationBlocked as exc:
        status = 'blocked'
        details = {'os_setting': exc.setting, 'display_verified': False}
    except Exception:
        status = "failed"
    with conn:
        conn.executemany("UPDATE notifications SET status=? WHERE proposal_id=?", [(status, key) for key in ids])
    return {"count": len(ids), **details, "status": status, **link}
