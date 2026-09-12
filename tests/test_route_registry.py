#!/usr/bin/env python3
"""Tests for the portable model-first route registry."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dispatch.route_registry import RouteConfigError, RouteRegistry


BASE_CONFIG = {
    "schema": "dispatch-routes.v1",
    "routes": {
        "openai/gpt-5.6-luna": {
            "default_harness": "codex",
            "target": "cluster",
            "kinds": ["review", "implement"],
            "fallback": {
                "policy": "remote-unavailable",
                "harness": "codex",
                "target": "direct",
            },
        },
        "google/gemma-4": {
            "default_harness": "llama-broker",
            "target": "cluster",
            "kinds": ["review"],
            "fallback": {"policy": "none"},
        },
    },
}


class RouteRegistryTests(unittest.TestCase):
    def test_public_example_configuration_loads(self) -> None:
        example = Path(__file__).resolve().parents[1] / "dispatch" / "routes.example.json"
        registry = RouteRegistry.from_dict(json.loads(example.read_text(encoding="utf-8")))
        self.assertEqual(registry.resolve("example/model", "review").target, "local")

    def test_remote_fallback_rejects_compatible_override_noop(self) -> None:
        invalid = {
            "schema": "dispatch-routes.v1",
            "routes": {
                "model": {
                    "default_harness": "codex",
                    "target": "cluster",
                    "harnesses": ["codex", "opencode"],
                    "kinds": ["review"],
                    "fallback": {
                        "policy": "remote-unavailable",
                        "harness": "opencode",
                        "target": "cluster",
                    },
                }
            },
        }
        with self.assertRaisesRegex(RouteConfigError, "must change target"):
            RouteRegistry.from_dict(invalid)

    def test_registry_routes_are_immutable(self) -> None:
        registry = RouteRegistry.from_dict(BASE_CONFIG)
        with self.assertRaises(TypeError):
            registry.routes["new-model"] = registry.routes["openai/gpt-5.6-luna"]  # type: ignore[index]
        self.assertIsInstance(hash(registry), int)

    def test_explicit_default_harness_is_not_a_deviation(self) -> None:
        route = RouteRegistry.from_dict(BASE_CONFIG).resolve(
            "openai/gpt-5.6-luna", "review", harness="codex"
        )
        self.assertFalse(route.harness_overridden)

    def test_model_first_resolution_records_effective_route(self) -> None:
        route = RouteRegistry.from_dict(BASE_CONFIG).resolve("openai/gpt-5.6-luna", "review")
        self.assertEqual(route.model, "openai/gpt-5.6-luna")
        self.assertEqual(route.harness, "codex")
        self.assertEqual(route.target, "cluster")
        self.assertFalse(route.harness_overridden)
        self.assertEqual(route.fallback.policy, "remote-unavailable")
        self.assertEqual(route.to_dict()["fallback"], {
            "policy": "remote-unavailable",
            "harness": "codex",
            "target": "direct",
        })

    def test_remote_fallback_cannot_repeat_the_same_route(self) -> None:
        invalid = {
            "schema": "dispatch-routes.v1",
            "routes": {
                "model": {
                    "default_harness": "codex",
                    "target": "cluster",
                    "harnesses": ["codex"],
                    "kinds": ["review"],
                    "fallback": {
                        "policy": "remote-unavailable",
                        "harness": "codex",
                        "target": "cluster",
                    },
                }
            },
        }
        with self.assertRaisesRegex(RouteConfigError, "must change target"):
            RouteRegistry.from_dict(invalid)

    def test_route_names_reject_control_characters(self) -> None:
        invalid = {**BASE_CONFIG, "routes": {"bad\x1bmodel": BASE_CONFIG["routes"]["google/gemma-4"]}}
        with self.assertRaisesRegex(RouteConfigError, "printable"):
            RouteRegistry.from_dict(invalid)

    def test_route_validation_branches_fail_closed(self) -> None:
        cases = (
            ({**BASE_CONFIG["routes"]["google/gemma-4"], "unexpected": True}, "unknown="),
            ({**BASE_CONFIG["routes"]["google/gemma-4"], "fallback": {"policy": "none", "target": "local"}}, "policy none"),
            ({**BASE_CONFIG["routes"]["google/gemma-4"], "harnesses": ["other"]}, "default_harness"),
            ({**BASE_CONFIG["routes"]["google/gemma-4"], "kinds": ["review", "review"]}, "duplicates"),
            ({**BASE_CONFIG["routes"]["google/gemma-4"], "fallback": {"policy": "remote-unavailable", "harness": "other", "target": "direct"}}, "fallback harness"),
        )
        for route, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(RouteConfigError, message):
                RouteRegistry.from_dict({"schema": "dispatch-routes.v1", "routes": {"model": route}})

    def test_model_and_name_bounds_are_enforced(self) -> None:
        with self.assertRaises(RouteConfigError):
            RouteRegistry.from_dict({"schema": "dispatch-routes.v1", "routes": {"m" * 129: BASE_CONFIG["routes"]["google/gemma-4"]}})
        invalid = {**BASE_CONFIG, "routes": {"model": {**BASE_CONFIG["routes"]["google/gemma-4"], "default_harness": "bad name"}}}
        with self.assertRaises(RouteConfigError):
            RouteRegistry.from_dict(invalid)

    def test_compatible_harness_override_is_recorded(self) -> None:
        config = {**BASE_CONFIG}
        config["routes"] = {
            **BASE_CONFIG["routes"],
            "openai/gpt-5.6-luna": {
                **BASE_CONFIG["routes"]["openai/gpt-5.6-luna"],
                "harnesses": ["codex", "opencode"],
            },
        }
        route = RouteRegistry.from_dict(config).resolve(
            "openai/gpt-5.6-luna", "review", harness="opencode"
        )
        self.assertEqual(route.harness, "opencode")
        self.assertTrue(route.harness_overridden)

    def test_incompatible_harness_override_fails_closed(self) -> None:
        with self.assertRaisesRegex(RouteConfigError, "not approved"):
            RouteRegistry.from_dict(BASE_CONFIG).resolve(
                "google/gemma-4", "review", harness="codex"
            )

    def test_unsupported_kind_and_model_fail_closed(self) -> None:
        registry = RouteRegistry.from_dict(BASE_CONFIG)
        with self.assertRaises(RouteConfigError):
            registry.resolve("google/gemma-4", "implement")
        with self.assertRaisesRegex(RouteConfigError, "unknown model"):
            registry.resolve("unknown/model", "review")

    def test_gemma_route_has_no_fallback(self) -> None:
        route = RouteRegistry.from_dict(BASE_CONFIG).resolve("google/gemma-4", "review")
        self.assertEqual(route.fallback.policy, "none")
        self.assertIsNone(route.fallback.harness)
        self.assertIsNone(route.fallback.target)

    def test_unknown_schema_and_keys_are_rejected(self) -> None:
        with self.assertRaises(RouteConfigError):
            RouteRegistry.from_dict({"schema": "dispatch-routes.v2", "routes": {}})
        invalid = {**BASE_CONFIG, "unexpected": True}
        with self.assertRaises(RouteConfigError):
            RouteRegistry.from_dict(invalid)

    def test_remote_fallback_requires_explicit_target_and_harness(self) -> None:
        invalid = {
            "schema": "dispatch-routes.v1",
            "routes": {
                "model": {
                    "default_harness": "codex",
                    "target": "cluster",
                    "kinds": ["review"],
                    "fallback": {"policy": "remote-unavailable"},
                }
            },
        }
        with self.assertRaisesRegex(RouteConfigError, "fallback harness and target"):
            RouteRegistry.from_dict(invalid)


if __name__ == "__main__":
    unittest.main()
