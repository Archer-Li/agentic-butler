"""Strict local settings. Live tasks require separate, immutable per-task approval."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

DEFAULTS = {
    "version": 1,
    "database": "pco.sqlite3",
    "appearance": {"accent": "#d71941", "compact": True},
    "notifications": {"enabled": False, "backend": "browser", "cooldown_seconds": 60,
                      "quiet_hours": None},
    "approval": {"ttl_minutes": 60, "execution": "preview_only"},
    "triage": {"enabled": False, "consent": False, "max_calls": 10},
    "agent": {"enabled": False, "provider": "codex", "timeout_seconds": 60},
    "memory": {"enabled": False, "projects": [], "bundle_bytes": 4096,
               "max_records": 64, "max_observations": 1000, "debounce_seconds": 30},
    "projects": [],
}


def event_enabled(config, event) -> bool:
    """Apply the same tool/project policy to ingestion-derived state and actions.

    Manual events remain available for explicitly enabled project labels.
    Removing a tool hides its stored suggestions without deleting audit history.
    """
    project = next((p for p in config["projects"] if p["name"] == event["repo"]), None)
    if project is None:
        return False
    tool = {"codex-notify": "codex", "claude-stop": "claude"}.get(event["source"])
    return tool is None or tool in project["tools"]


def validate(data: object) -> dict:
    if isinstance(data, dict):
        data = deepcopy(data)
        for optional in ("agent", "memory"):
            if optional not in data:
                data[optional] = deepcopy(DEFAULTS[optional])
    if not isinstance(data, dict) or set(data) != set(DEFAULTS):
        raise ValueError("configuration must contain exactly the documented version-1 sections")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("unsupported configuration version")
    for section in ("appearance", "notifications", "approval", "triage", "agent", "memory"):
        if not isinstance(data[section], dict) or set(data[section]) != set(DEFAULTS[section]):
            raise ValueError(f"invalid {section} fields")
    if not isinstance(data["database"], str) or not data["database"].strip():
        raise ValueError("database must be a nonempty path")
    appearance = data["appearance"]
    if not isinstance(appearance["accent"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", appearance["accent"]):
        raise ValueError("accent must be a six-digit hex color")
    for value in (appearance["compact"], data["notifications"]["enabled"], data["triage"]["enabled"], data["triage"]["consent"]):
        if type(value) is not bool:
            raise ValueError("switches must be boolean")
    notices = data["notifications"]
    if notices["backend"] not in ("browser", "native", "dialog"):
        raise ValueError("notification backend must be browser, native or dialog")
    for value, low, high, label in (
        (notices["cooldown_seconds"], 30, 86400, "cooldown"),
        (data["approval"]["ttl_minutes"], 1, 120, "approval TTL"),
        (data["triage"]["max_calls"], 1, 100, "triage call ceiling")):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"invalid {label}")
    quiet = notices["quiet_hours"]
    if quiet is not None and (not isinstance(quiet, list) or len(quiet) != 2 or any(
            not isinstance(v, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", v) for v in quiet)):
        raise ValueError("quiet_hours must be null or [HH:MM, HH:MM] in local time")
    if data["approval"]["execution"] != "preview_only":
        raise ValueError("real execution cannot be enabled by configuration")
    if data["triage"]["enabled"] and not data["triage"]["consent"]:
        raise ValueError("enabled triage requires consent")
    agent = data["agent"]
    if type(agent["enabled"]) is not bool or agent["provider"] != "codex":
        raise ValueError("agent must be an explicitly enabled Codex text-only adapter")
    if type(agent["timeout_seconds"]) is not int or not 1 <= agent["timeout_seconds"] <= 120:
        raise ValueError("agent timeout must be 1-120 seconds")
    if not isinstance(data["projects"], list):
        raise ValueError("projects must be an array")
    labels, workspaces = set(), set()
    for project in data["projects"]:
        if not isinstance(project, dict) or set(project) != {"name", "workspace", "tools"}:
            raise ValueError("project fields must be name, workspace, tools")
        name, workspace, adapters = project["name"], project["workspace"], project["tools"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name) or name in labels:
            raise ValueError("project names must be unique simple labels")
        if not isinstance(workspace, str) or not Path(workspace).is_absolute():
            raise ValueError("project workspace must be absolute")
        import os
        normalized = os.path.normcase(os.path.normpath(workspace))
        if normalized in workspaces:
            raise ValueError("each workspace can have only one project label")
        if not isinstance(adapters, list) or not adapters or any(v not in ("codex", "claude") for v in adapters) or len(set(adapters)) != len(adapters):
            raise ValueError("tools must be a unique selection of codex and claude")
        labels.add(name)
        workspaces.add(normalized)
    memory = data['memory']
    if type(memory['enabled']) is not bool:
        raise ValueError('memory.enabled must be boolean')
    if (not isinstance(memory['projects'], list)
            or any(not isinstance(name, str) or name not in labels for name in memory['projects'])
            or len(set(memory['projects'])) != len(memory['projects'])):
        raise ValueError('memory projects must be unique configured project names')
    for key, low, high in (('bundle_bytes', 512, 6000), ('max_records', 1, 128),
                          ('max_observations', 10, 10000), ('debounce_seconds', 0, 3600)):
        if type(memory[key]) is not int or not low <= memory[key] <= high:
            raise ValueError('invalid memory.' + key)
    return deepcopy(data)


def load(path: str | Path) -> dict:
    config_path = Path(path).expanduser().resolve()
    if config_path.stat().st_size > 65536:
        raise ValueError("configuration exceeds 64 KiB")
    data = validate(json.loads(config_path.read_text(encoding="utf-8")))
    db = Path(data["database"]).expanduser()
    data["database"] = str(db if db.is_absolute() else config_path.parent / db)
    return data


def initialize(path: str | Path) -> dict:
    result = deepcopy(DEFAULTS)
    with Path(path).open("x", encoding="utf-8") as target:
        target.write(json.dumps(result, indent=2) + "\n")
    return result
