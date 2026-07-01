"""Contract tests for parse_run_line against evo's real `evo run` output lines.

Run from evo-bridge/:  PYTHONPATH=src python3 -m pytest tests/ -v
or with unittest:      PYTHONPATH=src python3 -m unittest discover tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evo_mcp.cli import parse_run_line, _first_status_line  # noqa: E402


class TestParseRunLine(unittest.TestCase):
    def test_committed_with_delta(self):
        out = parse_run_line("COMMITTED exp_0007 0.84+0.06")
        self.assertEqual(out["outcome"], "committed")
        self.assertEqual(out["exp_id"], "exp_0007")
        self.assertAlmostEqual(out["score"], 0.84)
        self.assertAlmostEqual(out["delta"], 0.06)
        self.assertTrue(out["ok"])

    def test_committed_negative_delta_token(self):
        out = parse_run_line("COMMITTED exp_0010 0.50-0.02")
        self.assertEqual(out["outcome"], "committed")
        self.assertAlmostEqual(out["score"], 0.50)
        self.assertAlmostEqual(out["delta"], -0.02)

    def test_evaluated_regressed(self):
        out = parse_run_line("EVALUATED exp_0009 score=0.61 regressed")
        self.assertEqual(out["outcome"], "evaluated")
        self.assertEqual(out["exp_id"], "exp_0009")
        self.assertAlmostEqual(out["score"], 0.61)
        self.assertIn("regressed", out["detail"])
        self.assertFalse(out["ok"])

    def test_gate_failed_lists_gates(self):
        out = parse_run_line("GATE_FAILED refund_flow latency_floor")
        self.assertEqual(out["outcome"], "gate_failed")
        self.assertNotIn("exp_id", out)
        self.assertIn("refund_flow", out["detail"])
        self.assertIn("latency_floor", out["detail"])
        self.assertFalse(out["ok"])

    def test_failed_with_reason(self):
        out = parse_run_line("FAILED exp_0011 remote_infra_failure:container died")
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["exp_id"], "exp_0011")
        self.assertIn("remote_infra_failure", out["detail"])

    def test_check_passed(self):
        out = parse_run_line("CHECK_PASSED exp_0005 score=0.7 artifacts=/tmp/x")
        self.assertEqual(out["outcome"], "check_passed")
        self.assertAlmostEqual(out["score"], 0.7)
        self.assertTrue(out["ok"])

    def test_recovering(self):
        out = parse_run_line("RECOVERING exp_0012 attempt=2 process=123 state=running")
        self.assertEqual(out["outcome"], "recovering")
        self.assertEqual(out["exp_id"], "exp_0012")
        self.assertFalse(out["ok"])

    def test_unknown_line_degrades(self):
        out = parse_run_line("some unexpected output")
        self.assertEqual(out["outcome"], "unknown")
        self.assertFalse(out["ok"])

    def test_empty_line(self):
        out = parse_run_line("")
        self.assertEqual(out["outcome"], "unknown")


class TestFirstStatusLine(unittest.TestCase):
    def test_skips_progress_lines(self):
        stdout = (
            "preparing worktree...\n"
            "running benchmark (task 1/3)\n"
            "running benchmark (task 3/3)\n"
            "COMMITTED exp_0007 0.84+0.06\n"
        )
        self.assertEqual(_first_status_line(stdout), "COMMITTED exp_0007 0.84+0.06")

    def test_falls_back_to_last_nonempty(self):
        stdout = "just\nlog\nnoise\n"
        self.assertEqual(_first_status_line(stdout), "noise")

    def test_empty(self):
        self.assertEqual(_first_status_line("   \n  \n"), "")


if __name__ == "__main__":
    unittest.main()
