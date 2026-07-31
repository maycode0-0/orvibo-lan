"""Structural contracts for validation and release workflows."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHA_ACTION = re.compile(r"^[\w.-]+(?:/[\w.-]+)+@[0-9a-f]{40}$")


def load_workflow(name: str) -> dict[str, object]:
    """Load JSON, a strict YAML subset accepted by GitHub Actions."""
    return json.loads((WORKFLOWS / name).read_text(encoding="utf-8"))


def action_references(workflow: dict[str, object]) -> list[str]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    references: list[str] = []
    for job in jobs.values():
        assert isinstance(job, dict)
        steps = job.get("steps", [])
        assert isinstance(steps, list)
        for step in steps:
            assert isinstance(step, dict)
            reference = step.get("uses")
            if isinstance(reference, str):
                references.append(reference)
    return references


class ReleaseWorkflowTests(unittest.TestCase):
    """Verify triggers, validation duties, and release safety semantics."""

    def test_validate_owns_all_required_checks(self) -> None:
        workflow = load_workflow("validate.yml")
        triggers = workflow["on"]
        jobs = workflow["jobs"]
        assert isinstance(triggers, dict)
        assert isinstance(jobs, dict)
        self.assertTrue({"push", "pull_request"}.issubset(triggers))
        self.assertEqual(triggers["workflow_call"], {})
        self.assertTrue(
            {"quality", "tests", "package-contract", "hacs", "hassfest", "actionlint"}.issubset(
                jobs
            )
        )
        self.assertFalse((WORKFLOWS / "hacs.yml").exists())

        commands = "\n".join(
            str(step.get("run", ""))
            for job in jobs.values()
            if isinstance(job, dict)
            for step in job.get("steps", [])
            if isinstance(step, dict)
        )
        self.assertIn("ruff check custom_components tests", commands)
        self.assertIn("mypy custom_components/orvibo_lan", commands)
        self.assertIn("pytest --cov=custom_components.orvibo_lan", commands)
        self.assertIn("test_package_contract.py", commands)
        self.assertIn("test_release_workflow.py", commands)

        references = action_references(workflow)
        self.assertTrue(any(reference.startswith("hacs/action@") for reference in references))
        self.assertTrue(
            any(
                reference.startswith("home-assistant/actions/hassfest@") for reference in references
            )
        )
        self.assertTrue(any(reference.startswith("rhysd/actionlint@") for reference in references))
        self.assertTrue(
            all(SHA_ACTION.fullmatch(reference) for reference in references), references
        )

    def test_release_is_manifest_driven_and_repair_only_on_main(self) -> None:
        workflow = load_workflow("release.yml")
        triggers = workflow["on"]
        jobs = workflow["jobs"]
        assert isinstance(triggers, dict)
        assert isinstance(jobs, dict)
        self.assertEqual(
            triggers["push"],
            {
                "branches": ["main"],
                "tags": ["v*.*.*"],
                "paths": ["custom_components/orvibo_lan/manifest.json"],
            },
        )
        self.assertEqual(triggers["workflow_dispatch"], {})
        self.assertEqual(set(jobs), {"validate", "verify", "release"})
        validate = jobs["validate"]
        verify = jobs["verify"]
        release = jobs["release"]
        assert isinstance(validate, dict)
        assert isinstance(verify, dict)
        assert isinstance(release, dict)
        self.assertEqual(validate["uses"], "./.github/workflows/validate.yml")
        self.assertNotIn("if", verify)
        self.assertEqual(release["needs"], ["validate", "verify"])
        self.assertEqual(release["permissions"], {"contents": "write"})

        steps = release["steps"]
        assert isinstance(steps, list)
        tag_step = next(
            step for step in steps if isinstance(step, dict) and step.get("name") == "Create release tag on main"
        )
        self.assertEqual(
            tag_step["if"],
            "github.event_name == 'push' && github.ref == 'refs/heads/main'",
        )
        self.assertIn("git.createRef", str(tag_step["with"]["script"]))
        scripts = "\n".join(
            str(step.get("run", "")) + "\n" + str(step.get("with", {}).get("script", ""))
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("with", {}), dict)
        )
        self.assertIn("custom_components/orvibo_lan/manifest.json", scripts)
        self.assertIn("context.ref.startsWith('refs/tags/')", scripts)
        self.assertIn("context.ref !== 'refs/heads/main'", scripts)
        self.assertIn("refs/tags/$RELEASE_TAG^{commit}", scripts)
        self.assertIn("name: 'orvibo_lan.zip'", scripts)
        self.assertIn("deleteReleaseAsset", scripts)
        self.assertIn("uploadReleaseAsset", scripts)
        self.assertNotIn("github.event.inputs", scripts)
        self.assertTrue(
            all(SHA_ACTION.fullmatch(reference) for reference in action_references(workflow))
        )


if __name__ == "__main__":
    unittest.main()
