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
    inline_job,
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
    "command": ["fetch-mail", "--env-file", "{{env_file}}"],
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
        for cron in ("0 4 * *", "0 4 * * $(x)"):
            with self.subTest(cron=cron), self.assertRaisesRegex(ContractError, "five cron fields"):
                JobDefinition.from_dict(definition(PROMPT_DEFINITION, cron=cron))

    def test_rejects_unknown_timezone(self) -> None:
        with self.assertRaisesRegex(ContractError, "IANA"):
            JobDefinition.from_dict(definition(PROMPT_DEFINITION, timezone="Not/AZone"))

    def test_rejects_placeholder_in_shell_text_command(self) -> None:
        for command in ("fetch-mail {{env_file}}", 'echo --msg="run {{env_file}} now"', 'sh -c "echo {{env_file}}"'):
            with self.subTest(command=command), self.assertRaisesRegex(ContractError, "argument list"):
                JobDefinition.from_dict(definition(COMMAND_DEFINITION, command=command))

    def test_rejects_placeholder_sharing_an_argument(self) -> None:
        for argument in ("run {{env_file}} now", "--env={{env_file}}", '"{{env_file}}"'):
            command = ["fetch-mail", argument]
            with self.subTest(argument=argument), self.assertRaisesRegex(ContractError, "whole placeholder"):
                JobDefinition.from_dict(definition(COMMAND_DEFINITION, command=command))

    def test_rejects_host_dependent_timezone(self) -> None:
        for zone in ("localtime", "posixrules", "Factory"):
            with self.subTest(zone=zone), self.assertRaisesRegex(ContractError, "IANA"):
                JobDefinition.from_dict(definition(PROMPT_DEFINITION, timezone=zone))

    def test_rejects_placeholder_in_a_field_that_is_never_filled(self) -> None:
        for key, value in (("model", "{{post_to}}"), ("mcp_config", ["{{post_to}}"])):
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, f"{key} cannot hold a placeholder"):
                JobDefinition.from_dict(definition(PROMPT_DEFINITION, **{key: value}))

    def test_rejects_command_arguments_that_could_never_resolve(self) -> None:
        with self.assertRaisesRegex(ContractError, "total at most 4096"):
            JobDefinition.from_dict(definition(COMMAND_DEFINITION, command=["a" * 3000, "b" * 3000, "{{env_file}}"]))

    def test_unhashable_values_raise_contract_error(self) -> None:
        cases = [definition(PROMPT_DEFINITION, post_mode=[]),
                 definition(PROMPT_DEFINITION, overridable=[["cron"]])]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ContractError):
                JobDefinition.from_dict(data)


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

    def test_rejects_control_characters_in_tenant_values(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        data["jobs"]["digest"]["values"]["post_to"] = "12\n3"
        with self.assertRaisesRegex(ContractError, "control characters"):
            JobEnablement.from_dict(data)

    def test_rejects_placeholder_in_any_inline_field(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        data["jobs"]["reminder"]["inline"]["model"] = "{{model}}"
        with self.assertRaisesRegex(ContractError, "cannot use placeholders"):
            JobEnablement.from_dict(data)

    def test_rejects_definition_without_description(self) -> None:
        data = definition(PROMPT_DEFINITION)
        del data["description"]
        with self.assertRaisesRegex(ContractError, "description is required"):
            JobDefinition.from_dict(data)

    def test_rejects_badly_named_value(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        data["jobs"]["digest"]["values"]["Post-To"] = "1"
        with self.assertRaisesRegex(ContractError, "snake case"):
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
        job = resolve_job("example-plugin", JobDefinition.from_dict(PROMPT_DEFINITION), self.jobs["digest"])
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
            resolve_job("example-plugin", JobDefinition.from_dict(PROMPT_DEFINITION), entry)

    def test_resolve_refuses_override_the_definition_does_not_offer(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        data["jobs"]["digest"]["overrides"] = {"timeout_seconds": 60}
        entry = JobEnablement.from_dict(data).jobs["digest"]
        with self.assertRaisesRegex(ContractError, "does not allow overriding"):
            resolve_job("example-plugin", JobDefinition.from_dict(PROMPT_DEFINITION), entry)

    def test_command_values_cannot_add_shell_syntax(self) -> None:
        for hostile in ("/tmp/env; touch /tmp/owned", "$(touch /tmp/owned)", "`id`", "a\"b'c", "{{env_file}}"):
            data = {
                "schema": "scheduled-job-enablement.v1",
                "jobs": {"mail": {"use": "example-plugin/fetch-mail", "values": {"env_file": hostile}}},
            }
            entry = JobEnablement.from_dict(data).jobs["mail"]
            job = resolve_job("example-plugin", JobDefinition.from_dict(COMMAND_DEFINITION), entry)
            with self.subTest(hostile=hostile):
                self.assertEqual(shlex.split(job["command"]), ["fetch-mail", "--env-file", hostile])
                self.assertEqual(job["env"]["MAIL_ENV"], hostile)

    def test_inline_job_joins_an_argument_list(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        inline = data["jobs"]["reminder"]["inline"]
        del inline["prompt"]
        inline["command"] = ["notify", "weekly reminder"]
        job = inline_job(JobEnablement.from_dict(data).jobs["reminder"])
        self.assertEqual(shlex.split(job["command"]), ["notify", "weekly reminder"])
        self.assertTrue(job["one_shot"])

    def test_resolve_refuses_entry_for_another_plugin(self) -> None:
        with self.assertRaisesRegex(ContractError, "does not use this definition"):
            resolve_job("other-plugin", JobDefinition.from_dict(PROMPT_DEFINITION), self.jobs["digest"])

    def test_resolved_job_keeps_field_bounds(self) -> None:
        data = copy.deepcopy(ENABLEMENT)
        data["jobs"]["digest"]["values"]["post_to"] = "9" * 4000
        entry = JobEnablement.from_dict(data).jobs["digest"]
        with self.assertRaisesRegex(ContractError, "resolved post_to"):
            resolve_job("example-plugin", JobDefinition.from_dict(PROMPT_DEFINITION), entry)

    def test_resolve_refuses_entry_for_another_job(self) -> None:
        with self.assertRaisesRegex(ContractError, "does not use this definition"):
            resolve_job("example-plugin", JobDefinition.from_dict(COMMAND_DEFINITION), self.jobs["digest"])


if __name__ == "__main__":
    unittest.main()
