"""Thin, dependency-free wrapper around the `evo` CLI.

This module is deliberately free of any MCP imports so it can be unit-tested
in isolation and reused by non-MCP callers. It runs `evo` subcommands in a
given workspace and parses their *real* output contracts:

  - JSON-emitting commands (`new`, `frontier`, `show`, `get`, `awaiting`,
    `report --json`): parsed via json.loads.
  - The `run` command: emits a single status line whose first token is one of
    COMMITTED / EVALUATED / GATE_FAILED / FAILED / CHECK_PASSED / CHECK_FAILED.
    `parse_run_line` normalizes that into a structured dict.
  - Text commands (`scratchpad`, `status`): returned verbatim as text.

The output contracts mirrored here were read from evo's source
(plugins/evo/src/evo/cli.py) at evo 0.6.x. If evo changes a contract, the
fixture-based tests in tests/ are where the drift is caught.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class EvoError(RuntimeError):
    """A non-zero `evo` exit, or a contract-parse failure."""

    def __init__(self, message: str, *, returncode: int | None = None,
                 stdout: str = "", stderr: str = "", argv: list[str] | None = None):
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.argv = argv or []


@dataclass
class EvoCLI:
    """Run `evo` subcommands in a fixed workspace.

    Parameters
    ----------
    workspace:
        Absolute path to the evo workspace root (the directory containing
        `.evo/`). All commands run with this as cwd.
    evo_bin:
        Name or path of the evo executable. Defaults to "evo" on PATH.
    timeout:
        Per-command wall-clock ceiling in seconds. `run`/`wait` commands can
        legitimately run long; callers override per call.
    env:
        Extra environment variables merged over os.environ for every call.
    """

    workspace: str
    evo_bin: str = "evo"
    timeout: float | None = 600.0
    env: dict[str, str] = field(default_factory=dict)

    # ---- low-level execution ------------------------------------------- #

    def _resolve_bin(self) -> str:
        if os.sep in self.evo_bin:
            # Explicit path: accept only if it exists and is executable.
            p = Path(self.evo_bin)
            found = str(p) if (p.exists() and os.access(p, os.X_OK)) else None
        else:
            found = shutil.which(self.evo_bin)
        if not found:
            raise EvoError(
                f"evo executable {self.evo_bin!r} not found on PATH. "
                "Install with `uv tool install evo-hq-cli` and run "
                "`evo install <host>`, or pass evo_bin=/abs/path/to/evo."
            )
        return found

    def raw(self, args: list[str], *, timeout: float | None = None,
            check: bool = True) -> subprocess.CompletedProcess:
        """Execute `evo <args>` and return the CompletedProcess.

        Raises EvoError on non-zero exit when check=True.
        """
        argv = [self._resolve_bin(), *args]
        merged_env = {**os.environ, **self.env}
        try:
            proc = subprocess.run(
                argv,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.timeout,
                env=merged_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise EvoError(
                f"evo {' '.join(args)} timed out after {exc.timeout}s",
                argv=argv,
            ) from exc
        if check and proc.returncode != 0:
            raise EvoError(
                f"evo {' '.join(args)} exited {proc.returncode}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}",
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                argv=argv,
            )
        return proc

    def json(self, args: list[str], *, timeout: float | None = None) -> Any:
        """Execute a JSON-emitting `evo` subcommand and parse stdout."""
        proc = self.raw(args, timeout=timeout)
        text = proc.stdout.strip()
        if not text:
            raise EvoError(
                f"evo {' '.join(args)} produced no stdout to parse as JSON",
                stdout=proc.stdout, stderr=proc.stderr, argv=args,
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise EvoError(
                f"evo {' '.join(args)} stdout was not valid JSON: {exc}",
                stdout=proc.stdout, stderr=proc.stderr, argv=args,
            ) from exc

    def text(self, args: list[str], *, timeout: float | None = None) -> str:
        """Execute a text-emitting `evo` subcommand; return stdout verbatim."""
        return self.raw(args, timeout=timeout).stdout

    # ---- high-level, contract-aware commands --------------------------- #

    def status(self) -> str:
        return self.text(["status"])

    def scratchpad(self) -> str:
        return self.text(["scratchpad"])

    def frontier(self, *, strategy: str | None = None, params: str | None = None,
                 seed: int | None = None) -> dict[str, Any]:
        """Return the frontier envelope: {strategy, nodes:[{id,score,rank,...}], generated_at, [seed]}."""
        args = ["frontier"]
        if strategy:
            args += ["--strategy", strategy]
        if params:
            args += ["--params", params]
        if seed is not None:
            args += ["--seed", str(seed)]
        return self.json(args)

    def new(self, parent: str, hypothesis: str, *,
            from_artifact: str | None = None) -> dict[str, Any]:
        """Allocate an experiment. Returns evo's `new` JSON (includes id + worktree)."""
        args = ["new", "--parent", parent, "-m", hypothesis]
        if from_artifact:
            args += ["--from-artifact", from_artifact]
        return self.json(args)

    def run(self, exp_id: str, *, check: bool = False,
            staged_new_files: bool = False,
            timeout: float | None = None) -> dict[str, Any]:
        """Run (or --check) an experiment. Returns parse_run_line(...) of the status line.

        `evo run` exits non-zero on GATE_FAILED/FAILED, which is a normal
        experiment outcome, not a tooling error -- so we do not raise on
        non-zero here; the parsed `outcome` carries the verdict.
        """
        args = ["run", exp_id]
        if check:
            args.append("--check")
        if staged_new_files:
            args += ["--i-staged-new-files", "yes"]
        proc = self.raw(args, timeout=timeout, check=False)
        line = _first_status_line(proc.stdout)
        parsed = parse_run_line(line)
        parsed["returncode"] = proc.returncode
        parsed["stdout"] = proc.stdout
        if proc.stderr.strip():
            parsed["stderr_tail"] = proc.stderr.strip()[-2000:]
        return parsed

    def show(self, exp_id: str) -> dict[str, Any]:
        return self.json(["show", exp_id])

    def awaiting(self) -> list[dict[str, Any]]:
        return self.json(["awaiting"])

    def discards(self, like: str | None = None) -> str:
        args = ["discards"]
        if like:
            args += ["--like", like]
        return self.text(args)

    def get(self, field_name: str) -> Any:
        """`evo get <field>` emits JSON for the node/field."""
        return self.json(["get", field_name])

    def commit_decisions(self) -> None:  # pragma: no cover - placeholder
        raise NotImplementedError

    def prune(self, exp_id: str, *, invalid: bool = False, exhausted: bool = True,
              reason: str = "", yes: bool = False) -> str:
        args = ["prune", exp_id]
        if invalid:
            args.append("--invalid")
        elif exhausted:
            args.append("--exhausted")
        if reason:
            args += ["--reason", reason]
        if yes:
            args.append("--yes")
        return self.text(args)

    def discard(self, exp_id: str, *, reason: str,
                failure_class: str | None = None) -> str:
        args = ["discard", exp_id, "--reason", reason]
        if failure_class:
            args += ["--failure-class", failure_class]
        return self.text(args)

    def ship(self, exp_id: str | None = None, *, json_out: bool = False) -> Any:
        """Invoke `evo ship` (or report when ship is skill-driven).

        Kept generic: ship's exact surface is host/skill-mediated; here we just
        pass through. Callers that need the ship *skill* should drive it via the
        operator agent, not this CLI shim.
        """
        args = ["ship"]
        if exp_id:
            args.append(exp_id)
        if json_out:
            args.append("--json")
            return self.json(args)
        return self.text(args)

    def report(self, *, json_out: bool = True) -> Any:
        args = ["report"]
        if json_out:
            args.append("--json")
            return self.json(args)
        return self.text(args)

    def digest(self) -> dict[str, Any]:
        """Composite, machine-readable run summary for a downstream generalist.

        Assembles a single structured object from the read-only surfaces evo
        already exposes -- frontier, status, awaiting -- so a caller can render
        figures / a methods write-up without issuing five separate calls. This
        is the Gap-3 "expose the data, don't build the figure engine" endpoint.
        """
        digest: dict[str, Any] = {"workspace": self.workspace}
        # Each piece is best-effort: a fresh workspace may have no frontier yet.
        try:
            digest["frontier"] = self.frontier()
        except EvoError as exc:
            digest["frontier_error"] = str(exc)
        try:
            digest["status_text"] = self.status()
        except EvoError as exc:
            digest["status_error"] = str(exc)
        try:
            digest["awaiting"] = self.awaiting()
        except EvoError as exc:
            digest["awaiting_error"] = str(exc)
        return digest

    def graph(self) -> dict[str, Any]:
        """Read the active run's ``graph.json`` (all nodes) directly from disk.

        Pure file read -- no ``evo`` subprocess -- so the dashboard keeps
        rendering even mid-run and regardless of the CLI's availability. Locates
        the active run via ``.evo/meta.json``, falling back to the newest
        ``run_*/graph.json`` (or a legacy top-level one). Returns ``{"nodes": {}}``
        when nothing is found so callers never crash on a fresh workspace.
        """
        root = Path(self.workspace)
        evo = root / ".evo"
        candidates: list[Path] = []
        meta = evo / "meta.json"
        if meta.exists():
            try:
                active = json.loads(meta.read_text(encoding="utf-8")).get("active")
                if active:
                    candidates.append(evo / active / "graph.json")
            except (OSError, json.JSONDecodeError):
                pass
        candidates += sorted(evo.glob("run_*/graph.json"), reverse=True)
        candidates.append(evo / "graph.json")
        for g in candidates:
            if g.exists():
                try:
                    return json.loads(g.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
        return {"nodes": {}}


# --------------------------------------------------------------------------- #
# Pure parsers (no subprocess) -- the unit-testable contract layer.           #
# --------------------------------------------------------------------------- #

_RUN_OUTCOMES = {
    "COMMITTED", "EVALUATED", "GATE_FAILED", "FAILED",
    "CHECK_PASSED", "CHECK_FAILED", "PRE_GATE_FAILED", "RECOVERING",
}

_SCORE_RE = re.compile(r"score=(?P<score>-?\d+(?:\.\d+)?)")
_BARE_SCORE_RE = re.compile(r"^(?P<score>-?\d+(?:\.\d+)?)$")
_DELTA_RE = re.compile(r"(?P<delta>[+-]\d+(?:\.\d+)?)")


def _first_status_line(stdout: str) -> str:
    """Return the first line of stdout whose first token is a known outcome.

    `evo run` may print progress/log lines before the verdict; the verdict is
    the line that starts with one of the _RUN_OUTCOMES tokens. Falls back to the
    last non-empty line if no token line is found (so a contract change degrades
    to 'unknown' rather than crashing).
    """
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    for ln in lines:
        if ln.split(maxsplit=1)[0] in _RUN_OUTCOMES:
            return ln
    return lines[-1] if lines else ""


def parse_run_line(line: str) -> dict[str, Any]:
    """Normalize an `evo run` status line into a structured dict.

    Real contract examples (from evo cli.py):
      COMMITTED exp_0007 0.84+0.06
      EVALUATED exp_0009 score=0.61 regressed
      GATE_FAILED refund_flow latency_floor
      FAILED exp_0011 remote_infra_failure:container died
      CHECK_PASSED exp_0005 score=0.7 artifacts=/path
      RECOVERING exp_0012 attempt=2 process=... state=...

    Returns at minimum {outcome, raw}. Adds exp_id/score/delta/detail when present.
    """
    out: dict[str, Any] = {"raw": line}
    if not line:
        out["outcome"] = "unknown"
        return out
    tokens = line.split()
    head = tokens[0]
    if head in _RUN_OUTCOMES:
        out["outcome"] = head.lower()
        rest = tokens[1:]
    else:
        out["outcome"] = "unknown"
        rest = tokens

    # exp_id: first token matching exp_*
    for t in rest:
        if t.startswith("exp_"):
            out["exp_id"] = t
            break

    # score: prefer score=NN; else a bare numeric token (COMMITTED's "0.84+0.06")
    m = _SCORE_RE.search(line)
    if m:
        out["score"] = float(m.group("score"))
    else:
        for t in rest:
            bare = _BARE_SCORE_RE.match(t.split("+")[0].split("-")[0]) if t and t[0].isdigit() else None
            # COMMITTED prints "<score><signed-delta>" with no space, e.g. 0.84+0.06
            cm = re.match(r"^(?P<s>\d+(?:\.\d+)?)(?P<d>[+-]\d+(?:\.\d+)?)?$", t)
            if cm:
                out["score"] = float(cm.group("s"))
                if cm.group("d"):
                    out["delta"] = float(cm.group("d"))
                break

    # delta via score= form: trailing signed number on EVALUATED/COMMITTED
    if "delta" not in out:
        dm = _DELTA_RE.search(line)
        if dm and out.get("outcome") in {"committed", "evaluated"}:
            out["delta"] = float(dm.group("delta"))

    # detail: gate names / failure reason -- the non-id, non-numeric remainder
    detail = [t for t in rest
              if not t.startswith("exp_")
              and not _SCORE_RE.match(t)
              and not re.match(r"^score=", t)
              and not re.match(r"^\d+(?:\.\d+)?[+-]?\d*", t)]
    if detail:
        out["detail"] = " ".join(detail)

    out["ok"] = out["outcome"] in {"committed", "check_passed"}
    return out
