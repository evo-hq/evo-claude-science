"""Integration tests for EvoCLI using a stub `evo` executable (fixtures/fake_evo.py).

Exercises the subprocess + parse path end-to-end without a real evo install.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evo_mcp.cli import EvoCLI, EvoError  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_evo.py"


def _make_fake_evo_bin(tmpdir: str) -> str:
    """Create an `evo` shim on a temp dir that execs fake_evo.py, return its dir."""
    shim = Path(tmpdir) / "evo"
    shim.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(shim)


class TestEvoCLIIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = _make_fake_evo_bin(self.tmp)
        self.cli = EvoCLI(workspace=self.tmp, evo_bin=self.bin, timeout=30)

    def test_frontier_envelope(self):
        env = self.cli.frontier()
        self.assertEqual(env["strategy"]["kind"], "pareto_per_task")
        self.assertEqual(len(env["nodes"]), 2)
        self.assertEqual(env["nodes"][0]["id"], "exp_0006")

    def test_new_returns_id_and_worktree(self):
        out = self.cli.new("exp_0006", "add context pruning")
        self.assertEqual(out["id"], "exp_0021")
        self.assertTrue(out["worktree"].endswith("exp_0021"))

    def test_run_committed(self):
        out = self.cli.run("exp_0021")
        self.assertEqual(out["outcome"], "committed")
        self.assertEqual(out["exp_id"], "exp_0021")
        self.assertAlmostEqual(out["score"], 0.86)
        self.assertAlmostEqual(out["delta"], 0.02)
        self.assertEqual(out["returncode"], 0)
        self.assertTrue(out["ok"])

    def test_run_check(self):
        out = self.cli.run("exp_0021", check=True)
        self.assertEqual(out["outcome"], "check_passed")
        self.assertTrue(out["ok"])

    def test_show(self):
        out = self.cli.show("exp_0006")
        self.assertEqual(out["status"], "committed")
        self.assertEqual(out["children"], ["exp_0008", "exp_0005"])

    def test_awaiting(self):
        out = self.cli.awaiting()
        self.assertEqual(out[0]["id"], "exp_0014")

    def test_status_text(self):
        self.assertIn("best=0.84", self.cli.status())

    def test_digest_composes(self):
        d = self.cli.digest()
        self.assertEqual(d["workspace"], self.tmp)
        self.assertIn("frontier", d)
        self.assertIn("nodes", d["frontier"])
        self.assertIn("status_text", d)
        self.assertIn("awaiting", d)

    def test_missing_bin_raises(self):
        cli = EvoCLI(workspace=self.tmp, evo_bin="/nonexistent/evo-xyz")
        with self.assertRaises(EvoError):
            cli.status()


if __name__ == "__main__":
    unittest.main()
