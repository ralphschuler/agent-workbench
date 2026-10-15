"""Integrity checks for vendored skill packages and reusable local eval inputs."""

import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


class SkillPackageTests(unittest.TestCase):
    def test_vendored_packages_match_pinned_source_manifest(self):
        manifest = json.loads((SKILLS / "anthropic-provenance.json").read_text())
        self.assertEqual(manifest["repository"], "https://github.com/anthropics/skills")
        self.assertRegex(manifest["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(set(manifest["skills"]), {"frontend-design", "skill-creator"})
        for name, source in manifest["skills"].items():
            with self.subTest(skill=name):
                directory = SKILLS / name
                files = {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in directory.rglob("*") if path.is_file() and "__pycache__" not in path.parts}
                self.assertEqual(files, source["files"])
                self.assertIn("SKILL.md", files)
                self.assertIn("LICENSE.txt", files)
                self.assertIn("Apache License", (directory / "LICENSE.txt").read_text())

    def test_creator_resources_are_complete(self):
        directory = SKILLS / "skill-creator"
        for resource in ["agents/grader.md", "agents/comparator.md", "agents/analyzer.md", "references/schemas.md",
                         "eval-viewer/generate_review.py", "eval-viewer/viewer.html", "scripts/quick_validate.py",
                         "scripts/aggregate_benchmark.py", "assets/eval_review.html"]:
            with self.subTest(resource=resource):
                self.assertTrue((directory / resource).is_file())

    def test_local_skill_names_and_frontmatter(self):
        for name in ["desktop", "decisions", "memory"]:
            with self.subTest(skill=name):
                content = (SKILLS / name / "SKILL.md").read_text()
                match = re.match(r"\A---\n(.*?)\n---\n", content, re.DOTALL)
                self.assertIsNotNone(match)
                fields = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
                self.assertEqual(fields["name"].strip(), name)
                self.assertTrue(fields["description"].strip())
                self.assertLessEqual(len(fields["description"].strip()), 1024)
                self.assertLess(len(content.splitlines()), 500)

    def test_reusable_evals_reference_real_simulation_fixtures(self):
        for name in ["desktop", "decisions"]:
            with self.subTest(skill=name):
                directory = SKILLS / name
                evaluation = json.loads((directory / "evals" / "evals.json").read_text())
                self.assertEqual(evaluation["skill_name"], name)
                self.assertTrue(evaluation["evals"])
                for case in evaluation["evals"]:
                    self.assertGreaterEqual(len(case["expectations"]), 4)
                    self.assertIn("simulation", case["prompt"].lower())
                    for filename in case["files"]:
                        fixture = json.loads((directory / filename).read_text())
                        self.assertIn("simulation_only", fixture["evaluation_mode"])


if __name__ == "__main__":
    unittest.main()
