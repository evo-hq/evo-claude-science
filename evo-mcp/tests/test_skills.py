"""Tests for the evo-skill resolver (anti-drift: read evo's REAL SKILL.md).

Builds a fake plugin tree (skills/<name>/SKILL.md) and points the resolver at it
via EVO_PLUGIN_ROOT, so no real evo install is needed.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evo_mcp import skills  # noqa: E402


def _fake_plugin(tmp: str) -> str:
    root = Path(tmp) / "plugin"
    sk = root / "skills"
    for name in ("optimize", "subagent", "discover", "report", "ship"):
        d = sk / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n# {name} protocol\nbody for {name}\n")
    # optimize ships a workflows/ dir; discover ships references/
    (sk / "optimize" / "workflows").mkdir()
    (sk / "optimize" / "workflows" / "prose.md").write_text("# prose workflow\nstep 1\n")
    (sk / "discover" / "references").mkdir()
    (sk / "discover" / "references" / "constructing-benchmark.md").write_text("# benchmark\nrules\n")
    return str(root)


class TestSkillResolver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = _fake_plugin(self.tmp)
        self._prev = os.environ.get("EVO_PLUGIN_ROOT")
        os.environ["EVO_PLUGIN_ROOT"] = self.root

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("EVO_PLUGIN_ROOT", None)
        else:
            os.environ["EVO_PLUGIN_ROOT"] = self._prev

    def test_find_skills_dir(self):
        sd = skills.find_skills_dir()
        self.assertIsNotNone(sd)
        self.assertTrue((sd / "optimize" / "SKILL.md").is_file())

    def test_list_skills(self):
        r = skills.list_skills()
        self.assertTrue(r["ok"])
        names = {s["name"] for s in r["skills"]}
        self.assertEqual(names, {"optimize", "subagent", "discover", "report", "ship"})
        opt = next(s for s in r["skills"] if s["name"] == "optimize")
        self.assertTrue(opt["has_workflows"])
        self.assertFalse(opt["has_references"])

    def test_read_skill_real_content(self):
        r = skills.read_skill("optimize")
        self.assertTrue(r["ok"])
        self.assertIn("optimize protocol", r["skill_md"])
        self.assertFalse(r["truncated"])

    def test_read_skill_with_references(self):
        r = skills.read_skill("discover", include_references=True)
        self.assertTrue(r["ok"])
        self.assertIn("references", r)
        keys = list(r["references"])
        self.assertTrue(any("constructing-benchmark" in k for k in keys))

    def test_read_skill_truncation(self):
        r = skills.read_skill("optimize", max_chars=10)
        self.assertTrue(r["truncated"])
        self.assertEqual(len(r["skill_md"]), 10)

    def test_unknown_skill(self):
        r = skills.read_skill("nonsense")
        self.assertFalse(r["ok"])
        self.assertIn("available", r["reason"])

    def test_not_found_degrades(self):
        os.environ["EVO_PLUGIN_ROOT"] = "/nonexistent/plugin-xyz"
        r = skills.list_skills()
        self.assertFalse(r["ok"])
        self.assertIn("Could not locate", r["reason"])
        r2 = skills.read_skill("optimize")
        self.assertFalse(r2["ok"])


if __name__ == "__main__":
    unittest.main()
