#!/usr/bin/env python3
"""Tests for the scheduled-job definition and enablement contracts."""
from __future__ import annotations

import copy
import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.scheduled_job_contracts import (
    ContractError,
    JobDefinition,
    JobEnablement,
    resolve_job,
)

PROMPT_DEFINITION = {
    "schema": "scheduled-job-definition.v1",
    "id": "daily-digest",
    "description": "Summarize the prior day into one dated file.",
    "cron": "0 4 * * *",
    "timezone": "America/Los_Angeles",
    "timeout_seconds": 900,
    "prompt": "Write the digest into {{vault_path}}.",
    "model": "haiku",
    "post_mode": "webhook",
    "post_to": "{{post_to}}",
    "vault_sync": "{{vault_path}}",
    "requires": {
        "vault_path": "Checkout the job writes to.",
        "post_to": "Channel id for the run report.",
    },
    "overridable": ["cron", "model"],
}

COMMAND_DEFINITION = {
    "schema": "scheduled-job-definition.v1",
    "id": "fetch-mail",
    "description": "Fetch the prior day's mail.",
    "cron": "30 3 * * *",
    "timezone": "UTC",
    "timeout_seconds": 600,
    "command": "fetch-mail --env-file {{env_file}}",
    "env": {"MAIL_ENV": "{{env_file}}"},
    "requires": {"env_file": "Credential env file for the mailbox."},
}

ENABLEMENT = {
    "schema": "scheduled-job-enablement.v1",
    "jobs": {
        "digest": {
            "use": "example-plugin/daily-digest",
            "values": {"vault_path": "/srv/example-vault", "post_to": "123"},
            "overrides": {"cron": "15 4 * * *"},
        },
        "reminder": {
            "inline": {
                "cron": "0 9 * * 1",
                "timezone": "UTC",
                "timeout_seconds": 120,
                "prompt": "Post the weekly reminder.",
                "one_shot": True,
            },
        },
    },
}


def definition(base: dict, **changes) -> dict:
    data = copy.deepcopy(base)
    data.update(changes)
    return data


class DefinitionTests(unittest.TestCase):
    def test_valid_definitions_parse(self) -> None:
        self.assertEqual(JobDefinition.from_dict(PROMPT_DEFINITION).job_id, "daily-digest")
        self.assertEqual(JobDefinition.from_dict(COMMAND_DEFINITION).requires, {"env_file": "Credential env file for the mailbox."})

    def test_rejects_unknown_kind(self) -> None:
        data = definition(PROMPT_DEFINITION)
        data["script"] = data.pop("prompt")
        with self.assertRaisesRegex(ContractError, "unknown keys"):
            JobDefinition.from_dict(data)

    def test_rejects_both_prompt_and_command(self) -> None:
        with self.assertRaisesRegex(ContractError, "exactly one"):
            JobDefinition.from_dict(definition(PROMPT_DEFINITION, command="true"))

    def test_rejects_prompt_only_key_on_command_job(self) -> None:
        with self.assertRaisesRegex(ContractError, "prompt-only"):
            JobDefinition.from_dict(definition(COMMAND_DEFINITION, model="haiku"))

    def test_rejects_prompt_only_override_on_command_job(self) -> None:
        with self.assertRaisesRegex(ContractError, "prompt-only overrides"):
            JobDefinition.from_dict(definition(COMMAND_DEFINITION, overridable=["post_to"]))

    def test_rejects_undeclared_placeholder(self) -> None:
        with self.assertRaisesRegex(ContractError, "undeclared=\\['channel'\\]"):
            JobDefinition.from_dict(definition(PROMPT_DEFINITION, post_to="{{channel}}"))

    def test_rejects_unused_requirement(self) -> None:
        requires = dict(PROMPT_DEFINITION["requires"], extra="Never used.")
        with self.assertRaisesRegex(ContractError, "unused=\\['extra'\\]"):
            JobDefinition.from_dict(definition(PROMPT_DEFINITION, requires=requires))

    def test_rejects_tenant_only_key(self) -> None:
        with self.assertRaisesRegex(ContractError, "tenant-only"):
            JobDefinition.from_dict(definition(PROMPT_DEFINITION, enabled=False))

    def test_rejects_bad_cron(self) -> None:
        with self.assertRaisesRegex(ContractError, "five fields"):
            JobDefinition.from_dict(definition(PROMPT_DEFINITION, cron="0 4 * *"))


class EnablementTests(unittest.TestCase):
    def test_valid_enablement_parses(self) -> None:
        jobs = JobEnablement.from_dict(ENABLEMENT).jobs
        self.assertEqual(jobs["digest"].use, ("example-plugin", "daily-digest"))
        self.assertTrue(jobs["reminder"].inline["one_shot"])

    def test_rejects_override_of_unknown_key(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        data["jobs"]["digest"]["overrides"] = {"prompt": "something else"}
        with self.assertRaisesRegex(ContractError, "not an overridable key"):
            JobEnablement.from_dict(data)

    def test_rejects_entry_without_use_or_inline(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        data["jobs"]["digest"] = {"values": {}}
        with self.assertRaisesRegex(ContractError, "use or inline"):
            JobEnablement.from_dict(data)

    def test_rejects_placeholder_in_inline_job(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        data["jobs"]["reminder"]["inline"]["prompt"] = "Write to {{vault_path}}."
        with self.assertRaisesRegex(ContractError, "cannot use placeholders"):
            JobEnablement.from_dict(data)


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = JobEnablement.from_dict(ENABLEMENT).jobs

    def test_resolve_fills_values_and_applies_overrides(self) -> None:
        job = resolve_job(JobDefinition.from_dict(PROMPT_DEFINITION), self.jobs["digest"])
        self.assertEqual(job["prompt"], "Write the digest into /srv/example-vault.")
        self.assertEqual(job["vault_sync"], "/srv/example-vault")
        self.assertEqual(job["post_to"], "123")
        self.assertEqual(job["cron"], "15 4 * * *")
        self.assertTrue(job["enabled"])
        self.assertNotIn("description", job)

    def test_resolve_refuses_missing_value(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        del data["jobs"]["digest"]["values"]["post_to"]
        entry = JobEnablement.from_dict(data).jobs["digest"]
        with self.assertRaisesRegex(ContractError, "missing=\\['post_to'\\]"):
            resolve_job(JobDefinition.from_dict(PROMPT_DEFINITION), entry)

    def test_resolve_refuses_override_the_definition_does_not_offer(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        data["jobs"]["digest"]["overrides"] = {"timeout_seconds": 60}
        entry = JobEnablement.from_dict(data).jobs["digest"]
        with self.assertRaisesRegex(ContractError, "does not allow overriding"):
            resolve_job(JobDefinition.from_dict(PROMPT_DEFINITION), entry)

    def test_command_values_cannot_add_shell_syntax(self) -> None:
        hostile = "/tmp/env; touch /tmp/owned"
        data = {
            "schema": "scheduled-job-enablement.v1",
            "jobs": {"mail": {"use": "example-plugin/fetch-mail", "values": {"env_file": hostile}}},
        }
        entry = JobEnablement.from_dict(data).jobs["mail"]
        job = resolve_job(JobDefinition.from_dict(COMMAND_DEFINITION), entry)
        self.assertEqual(shlex.split(job["command"]), ["fetch-mail", "--env-file", hostile])
        self.assertEqual(job["env"]["MAIL_ENV"], hostile)

    def test_resolve_refuses_entry_for_another_job(self) -> None:
        with self.assertRaisesRegex(ContractError, "does not use this definition"):
            resolve_job(JobDefinition.from_dict(COMMAND_DEFINITION), self.jobs["digest"])


if __name__ == "__main__":
    unittest.main()
