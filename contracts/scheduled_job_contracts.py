"""Strict, transport-neutral contracts for scheduled jobs.

A plugin ships a `scheduled-job-definition.v1` for each job it offers. A tenant
writes one `scheduled-job-enablement.v1` file that turns shipped jobs on with its
own values, or declares a tenant-only job inline. `resolve_job` merges the two
into the flat job a runner executes. The runner owns posting and repository
writes; these contracts only describe what to run and when.
"""
from __future__ import annotations

import re
import shlex
import zoneinfo
from dataclasses import dataclass
from typing import Any, Callable, Mapping

DEFINITION_SCHEMA = "scheduled-job-definition.v1"
ENABLEMENT_SCHEMA = "scheduled-job-enablement.v1"


class ContractError(ValueError):
    """Raised when a scheduled-job contract is malformed or exceeds a bound."""


_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_VALUE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
_PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")
_CRON_FIELD = re.compile(r"[0-9A-Za-z*,/-]+")
_MAX_PROMPT = 64 * 1024
_MAX_JOBS = 200


def _text(value: Any, field: str, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ContractError(f"{field} must be a non-empty string of at most {max_length} characters")
    return value


def _bool(value: Any, field: str) -> None:
    if not isinstance(value, bool):
        raise ContractError(f"{field} must be a boolean")


def _positive_int(value: Any, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError(f"{field} must be a positive integer")


def _one_of(allowed: set[str]) -> Callable[[Any, str], None]:
    def check(value: Any, field: str) -> None:
        if not isinstance(value, str) or value not in allowed:
            raise ContractError(f"{field} must be one of {sorted(allowed)}")
    return check


def _cron(value: Any, field: str) -> None:
    parts = _text(value, field, 128).split()
    if len(parts) != 5 or not all(_CRON_FIELD.fullmatch(part) for part in parts):
        raise ContractError(f"{field} must have five cron fields")


# These load from the tz database but name the host's own setting or a stub,
# so a schedule using them would depend on the host it runs on.
_ZONES = zoneinfo.available_timezones() - {"Factory", "localtime", "posixrules"}


def _timezone(value: Any, field: str) -> None:
    if _text(value, field, 64) not in _ZONES:
        raise ContractError(f"{field} is not a known IANA time zone")


def _plain(value: Any, field: str) -> str:
    text = _text(value, field, 4096)
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        raise ContractError(f"{field} must not contain control characters")
    return text


def _command(value: Any, field: str) -> None:
    # Shell text cannot hold a placeholder safely: whether a quote applies
    # depends on shell grammar the contract does not parse. A templated
    # command is an argument list, and each placeholder is a whole argument.
    if isinstance(value, str):
        if "{{" in _text(value, field, 4096):
            raise ContractError(f"{field} as shell text cannot hold a placeholder; use an argument list")
        return
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise ContractError(f"{field} must be shell text or a list of 1 to 64 arguments")
    for index, argument in enumerate(value):
        _text(argument, f"{field}[{index}]", 4096)
        if "{{" in argument and not _PLACEHOLDER.fullmatch(argument):
            raise ContractError(f"{field}[{index}] must be a whole placeholder or hold none")
    if sum(len(argument) for argument in value) > 4096:
        raise ContractError(f"{field} arguments must total at most 4096 characters")


def _vault_sync(value: Any, field: str) -> None:
    if not isinstance(value, bool):
        _text(value, field)


def _mcp_config(value: Any, field: str) -> None:
    if value == "none":
        return
    if not isinstance(value, list) or len(value) > 32:
        raise ContractError(f"{field} must be \"none\" or a list of at most 32 server names")
    for index, name in enumerate(value):
        _text(name, f"{field}[{index}]", 128)


def _env(value: Any, field: str) -> None:
    if not isinstance(value, Mapping) or len(value) > 64:
        raise ContractError(f"{field} must be an object of at most 64 entries")
    for key, item in value.items():
        if not isinstance(key, str) or not _ENV_NAME.fullmatch(key):
            raise ContractError(f"{field} key {key!r} is not an environment variable name")
        if item != "":
            _plain(item, f"{field}.{key}")


_FIELDS: dict[str, Callable[[Any, str], object]] = {
    "description": lambda v, f: _text(v, f),
    "cron": _cron,
    "timezone": _timezone,
    "timeout_seconds": _positive_int,
    "prompt": lambda v, f: _text(v, f, _MAX_PROMPT),
    "command": _command,
    "model": lambda v, f: _text(v, f, 128),
    "post_mode": _one_of({"webhook", "direct"}),
    "post_to": lambda v, f: _text(v, f, 128),
    "working_dir": lambda v, f: _text(v, f),
    "vault_sync": _vault_sync,
    "mcp_config": _mcp_config,
    "env": _env,
    "dream": _bool,
    "dream_mode": _one_of({"off", "shadow", "gated"}),
    "max_post_chars": _positive_int,
    "expected_stdout_start": lambda v, f: _text(v, f, 256),
    "plain_bullets": _bool,
    "agent_log": _one_of({"all", "failures"}),
    "enabled": _bool,
    "one_shot": _bool,
}
# Only a prompt job produces output for the runner to post.
_PROMPT_ONLY = {"model", "post_mode", "post_to", "mcp_config", "max_post_chars", "expected_stdout_start", "plain_bullets"}
# A tenant turns a shipped job on or off; one_shot belongs to inline jobs only.
_TENANT_ONLY = {"enabled", "one_shot"}
_OVERRIDABLE = {"cron", "timezone", "model", "timeout_seconds", "post_mode", "post_to",
                "mcp_config", "dream", "dream_mode", "max_post_chars", "agent_log"}
_TEMPLATED = ("prompt", "post_to", "working_dir", "vault_sync")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object")
    return value


def _job_fields(data: Mapping[str, Any], field: str, extra: set[str]) -> dict[str, Any]:
    unknown = set(data) - set(_FIELDS) - extra
    if unknown:
        raise ContractError(f"{field} has unknown keys: {sorted(unknown)}")
    for key in ("cron", "timezone", "timeout_seconds"):
        if key not in data:
            raise ContractError(f"{field}.{key} is required")
    if ("prompt" in data) == ("command" in data):
        raise ContractError(f"{field} must set exactly one of prompt or command")
    if "command" in data and _PROMPT_ONLY & set(data):
        raise ContractError(f"{field} sets prompt-only keys on a command job: {sorted(_PROMPT_ONLY & set(data))}")
    fields = {key: value for key, value in data.items() if key not in extra}
    for key, value in fields.items():
        _FIELDS[key](value, f"{field}.{key}")
    return fields


def _template_strings(fields: Mapping[str, Any]) -> list[str]:
    strings = [fields[key] for key in _TEMPLATED if isinstance(fields.get(key), str)]
    strings += fields["command"] if isinstance(fields.get("command"), list) else []
    return strings + list((fields.get("env") or {}).values())


def _placeholders(fields: Mapping[str, Any]) -> set[str]:
    return {name for text in _template_strings(fields) for name in _PLACEHOLDER.findall(text)}


def _refuse_stray_placeholders(fields: Mapping[str, Any], field: str) -> None:
    # Only templated fields are ever filled; a placeholder anywhere else would
    # reach the runner as literal text.
    for key, value in fields.items():
        if key not in (*_TEMPLATED, "command", "env") and "{{" in repr(value):
            raise ContractError(f"{field}.{key} cannot hold a placeholder")


@dataclass(frozen=True)
class JobDefinition:
    job_id: str
    fields: Mapping[str, Any]
    requires: Mapping[str, str]
    overridable: frozenset[str]

    @classmethod
    def from_dict(cls, data: Any) -> "JobDefinition":
        data = _mapping(data, "job definition")
        if data.get("schema") != DEFINITION_SCHEMA:
            raise ContractError("unsupported job definition schema")
        job_id = data.get("id")
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise ContractError("id must be lowercase letters, digits, and hyphens, at most 64 characters")
        meta = {"schema", "id", "requires", "overridable"}
        if _TENANT_ONLY & set(data):
            raise ContractError(f"a shipped job cannot set tenant-only keys: {sorted(_TENANT_ONLY & set(data))}")
        fields = _job_fields(data, "job definition", meta)
        _text(fields.get("description"), "job definition.description")
        _refuse_stray_placeholders(fields, "job definition")
        requires = cls._requires(data.get("requires", {}))
        used = _placeholders(fields)
        if used != set(requires):
            raise ContractError(f"placeholders and requires differ, undeclared={sorted(used - set(requires))}, unused={sorted(set(requires) - used)}")
        overridable = data.get("overridable", [])
        if not isinstance(overridable, list) or not all(isinstance(key, str) and key in _OVERRIDABLE for key in overridable):
            raise ContractError(f"overridable must be a list drawn from {sorted(_OVERRIDABLE)}")
        if "command" in fields and _PROMPT_ONLY & set(overridable):
            raise ContractError("a command job cannot offer prompt-only overrides")
        return cls(job_id, fields, requires, frozenset(overridable))

    @staticmethod
    def _requires(value: Any) -> dict[str, str]:
        value = _mapping(value, "requires")
        for name, description in value.items():
            if not isinstance(name, str) or not _VALUE_NAME.fullmatch(name):
                raise ContractError(f"requires name {name!r} must be lowercase snake case")
            _text(description, f"requires.{name}")
        return dict(value)


@dataclass(frozen=True)
class JobEntry:
    use: tuple[str, str] | None
    values: Mapping[str, str]
    overrides: Mapping[str, Any]
    enabled: bool
    inline: Mapping[str, Any] | None


def _use_entry(data: Mapping[str, Any], field: str) -> JobEntry:
    unknown = set(data) - {"use", "values", "overrides", "enabled"}
    if unknown:
        raise ContractError(f"{field} has unknown keys: {sorted(unknown)}")
    plugin, _, job_id = _text(data["use"], f"{field}.use", 129).partition("/")
    if not _ID.fullmatch(plugin) or not _ID.fullmatch(job_id):
        raise ContractError(f"{field}.use must be <plugin>/<job-id>")
    values = _mapping(data.get("values", {}), f"{field}.values")
    for name, value in values.items():
        _plain(value, f"{field}.values.{name}")
    overrides = _mapping(data.get("overrides", {}), f"{field}.overrides")
    for key, value in overrides.items():
        if key not in _OVERRIDABLE:
            raise ContractError(f"{field}.overrides.{key} is not an overridable key")
        _FIELDS[key](value, f"{field}.overrides.{key}")
    enabled = data.get("enabled", True)
    _bool(enabled, f"{field}.enabled")
    return JobEntry((plugin, job_id), dict(values), dict(overrides), enabled, None)


def _inline_entry(data: Mapping[str, Any], field: str) -> JobEntry:
    if set(data) != {"inline"}:
        raise ContractError(f"{field} with inline must have no other keys")
    fields = _job_fields(_mapping(data["inline"], f"{field}.inline"), f"{field}.inline", set())
    if _placeholders(fields):
        raise ContractError(f"{field}.inline cannot use placeholders; write the value directly")
    return JobEntry(None, {}, {}, fields.get("enabled", True), fields)


@dataclass(frozen=True)
class JobEnablement:
    jobs: Mapping[str, JobEntry]

    @classmethod
    def from_dict(cls, data: Any) -> "JobEnablement":
        data = _mapping(data, "job enablement")
        if set(data) != {"schema", "jobs"} or data["schema"] != ENABLEMENT_SCHEMA:
            raise ContractError("job enablement must have exactly schema and jobs, with a supported schema")
        jobs = _mapping(data["jobs"], "jobs")
        if len(jobs) > _MAX_JOBS:
            raise ContractError(f"jobs must have at most {_MAX_JOBS} entries")
        entries = {}
        for key, entry in jobs.items():
            if not isinstance(key, str) or not _ID.fullmatch(key):
                raise ContractError(f"job key {key!r} must be lowercase letters, digits, and hyphens")
            entry = _mapping(entry, f"jobs.{key}")
            if "inline" not in entry and "use" not in entry:
                raise ContractError(f"jobs.{key} must set use or inline")
            parse = _inline_entry if "inline" in entry else _use_entry
            entries[key] = parse(entry, f"jobs.{key}")
        return cls(entries)


def _fill(text: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


def resolve_job(plugin: str, definition: JobDefinition, entry: JobEntry) -> dict[str, Any]:
    """Merge a shipped definition with a tenant entry into the job a runner executes.

    An argument-list `command` is joined with `shlex.join`, so each filled
    placeholder reaches the program as exactly one argument.
    """
    if entry.use != (plugin, definition.job_id):
        raise ContractError("entry does not use this definition")
    missing = set(definition.requires) - set(entry.values)
    unknown = set(entry.values) - set(definition.requires)
    if missing or unknown:
        raise ContractError(f"values invalid, missing={sorted(missing)}, unknown={sorted(unknown)}")
    refused = set(entry.overrides) - definition.overridable
    if refused:
        raise ContractError(f"the definition does not allow overriding {sorted(refused)}")
    job = {key: value for key, value in definition.fields.items() if key != "description"}
    for key in _TEMPLATED:
        if isinstance(job.get(key), str):
            job[key] = _fill(job[key], entry.values)
    if isinstance(job.get("command"), list):
        job["command"] = shlex.join(_fill(argument, entry.values) for argument in job["command"])
    if "env" in job:
        job["env"] = {name: _fill(value, entry.values) for name, value in job["env"].items()}
    job.update(entry.overrides)
    return _checked(job, entry.enabled)


def _checked(job: dict[str, Any], enabled: bool) -> dict[str, Any]:
    for key, value in job.items():
        # A joined command is shell text that may carry "{{" from a tenant value,
        # so it gets a length bound instead of the template rule.
        check = _FIELDS[key] if key != "command" else (lambda v, f: _text(v, f, _MAX_PROMPT))
        check(value, f"resolved {key}")
    job["enabled"] = enabled
    return job


def inline_job(entry: JobEntry) -> dict[str, Any]:
    """Return a tenant-only job in the same flat shape `resolve_job` produces."""
    if entry.inline is None:
        raise ContractError("entry is not an inline job")
    job = {key: value for key, value in entry.inline.items() if key != "description"}
    if isinstance(job.get("command"), list):
        job["command"] = shlex.join(job["command"])
    return _checked(job, entry.enabled)
