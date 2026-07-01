"""Tests for the self-healing bootstrap + doctor.

Covers detection, idempotency, dry-run provisioning, the not-a-git-repo
blocker, and the doctor contract handshake (against fixtures/fake_evo.py).
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evo_mcp.cli import EvoCLI  # noqa: E402
from evo_mcp import bootstrap as bs  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_evo.py"


def _fake_evo_bin(tmpdir: str) -> str:
    shim = Path(tmpdir) / "evo"
    shim.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(shim)


def _stub_git_repo(path: str):
    """Make bootstrap see `path` as a git repo without creating a real `.git`.

    The sandbox blocks creation of any `.git` path, so we patch the git-toplevel
    probe to report this dir as a repo root. Returns a cleanup callable.
    """
    orig = bs._git_toplevel
    bs._git_toplevel = lambda p: Path(path)  # noqa: ARG005
    return lambda: setattr(bs, "_git_toplevel", orig)


class TestDetect(unittest.TestCase):
    def test_plain_dir_not_initialized(self):
        tmp = tempfile.mkdtemp()
        det = bs.detect(tmp, evo_bin="/nonexistent/evo-xyz")
        self.assertFalse(det.evo_on_path)
        self.assertFalse(det.evo_initialized)
        self.assertFalse(det.has_baseline)

    def test_detects_git_repo(self):
        tmp = tempfile.mkdtemp()
        cleanup = _stub_git_repo(tmp)
        try:
            det = bs.detect(tmp, evo_bin="/nonexistent/evo-xyz")
            self.assertTrue(det.is_git_repo)
        finally:
            cleanup()

    def test_detects_initialized_with_committed_baseline(self):
        tmp = tempfile.mkdtemp()
        run = Path(tmp) / ".evo" / "run_0000"
        run.mkdir(parents=True)
        (run / "graph.json").write_text(json.dumps({
            "nodes": {"exp_0000": {"id": "exp_0000", "status": "committed"}}
        }))
        det = bs.detect(tmp, evo_bin="/nonexistent/evo-xyz")
        self.assertTrue(det.evo_initialized)
        self.assertTrue(det.has_baseline)

    def test_initialized_without_baseline(self):
        tmp = tempfile.mkdtemp()
        run = Path(tmp) / ".evo" / "run_0000"
        run.mkdir(parents=True)
        (run / "graph.json").write_text(json.dumps({
            "nodes": {"exp_0000": {"id": "exp_0000", "status": "active"}}
        }))
        det = bs.detect(tmp, evo_bin="/nonexistent/evo-xyz")
        self.assertTrue(det.evo_initialized)
        self.assertFalse(det.has_baseline)


class TestProvisionDryRun(unittest.TestCase):
    def test_dry_run_plans_install_when_missing(self):
        tmp = tempfile.mkdtemp()
        det = bs.detect(tmp, evo_bin="/nonexistent/evo-xyz")
        prov = bs.provision(det, dry_run=True)
        self.assertTrue(prov["ok"])
        self.assertEqual(len(prov["actions"]), 1)
        self.assertFalse(prov["actions"][0]["ran"])
        self.assertIn("evo-hq-cli", " ".join(prov["actions"][0]["cmd"]))

    def test_provision_noop_when_present(self):
        tmp = tempfile.mkdtemp()
        binpath = _fake_evo_bin(tmp)
        det = bs.detect(tmp, evo_bin=binpath)
        self.assertTrue(det.evo_on_path)
        prov = bs.provision(det, dry_run=True)
        self.assertTrue(prov["ok"])
        self.assertEqual(prov["actions"], [])


class TestInitialize(unittest.TestCase):
    def test_blocks_when_not_git(self):
        tmp = tempfile.mkdtemp()
        det = bs.detect(tmp, evo_bin="/nonexistent/evo-xyz")
        init = bs.initialize(det, dry_run=True)
        self.assertFalse(init["ok"])
        self.assertIn("git", init["reason"].lower())

    def test_dry_run_plans_init_on_git_repo(self):
        tmp = tempfile.mkdtemp()
        cleanup = _stub_git_repo(tmp)
        try:
            det = bs.detect(tmp, evo_bin="/nonexistent/evo-xyz")
            init = bs.initialize(det, dry_run=True)
            self.assertTrue(init["ok"])
            self.assertEqual(init["cmd"], ["evo", "init"])
        finally:
            cleanup()

    def test_noop_when_already_initialized(self):
        tmp = tempfile.mkdtemp()
        (Path(tmp) / ".evo").mkdir()
        det = bs.detect(tmp, evo_bin="/nonexistent/evo-xyz")
        init = bs.initialize(det, dry_run=True)
        self.assertTrue(init["ok"])
        self.assertFalse(init["ran"])


class TestDoctor(unittest.TestCase):
    def test_contracts_match_fake_evo(self):
        tmp = tempfile.mkdtemp()
        binpath = _fake_evo_bin(tmp)
        cli = EvoCLI(workspace=tmp, evo_bin=binpath, timeout=30)
        report = bs.doctor(cli)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["mismatches"], [])
        names = {c["name"] for c in report["checks"]}
        self.assertIn("evo frontier contract", names)

    def test_doctor_never_raises_on_bad_bin(self):
        tmp = tempfile.mkdtemp()
        cli = EvoCLI(workspace=tmp, evo_bin="/nonexistent/evo-xyz")
        report = bs.doctor(cli)   # must not raise
        self.assertFalse(report["ok"])


class TestBootstrapDryRun(unittest.TestCase):
    def test_full_dry_run_reaches_ready(self):
        tmp = tempfile.mkdtemp()
        cleanup = _stub_git_repo(tmp)
        binpath = _fake_evo_bin(tmp)
        try:
            result = bs.bootstrap(tmp, evo_bin=binpath, dry_run=True)
        finally:
            cleanup()
        self.assertTrue(result["ready"], result)
        self.assertEqual(result["phase"], "ready")
        self.assertIn("plan", result)
        # Fresh repo, fake evo has no real graph -> needs discover.
        self.assertTrue(result["plan"]["needs_discover"])

    def test_blocks_on_non_git(self):
        tmp = tempfile.mkdtemp()
        binpath = _fake_evo_bin(tmp)
        result = bs.bootstrap(tmp, evo_bin=binpath, dry_run=True)
        self.assertFalse(result["ready"])
        self.assertEqual(result["phase"], "initialize")


if __name__ == "__main__":
    unittest.main()
