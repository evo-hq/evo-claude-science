"""Self-healing setup for the evo bridge: detect -> provision -> init -> verify.

The goal is that a first-time user never assembles evo + the bridge by hand. The
agent (Claude Science or any MCP host) calls `bootstrap(...)` once; it makes the
environment ready and STOPS before spending compute on the optimization loop.
That last property is deliberate ("auto-setup, manual start"): installing
software and scaffolding a workspace is safe and idempotent; running an
unattended search that burns compute is a decision the user okays.

Every phase is:
  * idempotent  -- safe to re-run; already-done work is detected and skipped.
  * transparent -- returns a structured record of exactly what it did/would do.
  * fail-soft   -- a phase that can't complete reports `ok=False` + reason
                   rather than raising, so the agent can surface a clear next
                   step instead of a stack trace.

`dry_run=True` returns the planned shell commands without executing them, which
is how the provisioning logic is unit-tested without a live install.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .cli import EvoCLI, EvoError

EVO_PIP_PACKAGE = "evo-hq-cli"
SUPPORTED_HOSTS = (
    "claude-code", "codex", "cursor", "opencode", "openclaw", "hermes", "pi", "generic",
)


# --------------------------------------------------------------------------- #
# Phase 1: detect                                                             #
# --------------------------------------------------------------------------- #

@dataclass
class Detection:
    workspace: str
    evo_on_path: bool
    evo_bin: str | None
    is_git_repo: bool
    evo_initialized: bool          # .evo/ exists
    has_baseline: bool             # a committed root experiment exists
    host: str                      # detected agent runtime
    notes: list[str] = field(default_factory=list)


def _detect_host() -> str:
    """Best-effort agent-runtime detection from environment signals."""
    if os.environ.get("CLAUDE_SCIENCE") or os.environ.get("CLAUDE_SCIENCE_FRAME_ID"):
        return "claude-code"   # CS drives evo through the claude-code host plugin
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE"):
        return "claude-code"
    if os.environ.get("CODEX") or os.environ.get("CODEX_SANDBOX"):
        return "codex"
    if os.environ.get("CURSOR") or os.environ.get("CURSOR_AGENT"):
        return "cursor"
    return "generic"


def detect(workspace: str, evo_bin: str = "evo") -> Detection:
    ws = Path(workspace).resolve()
    found = shutil.which(evo_bin) if os.sep not in evo_bin else (
        evo_bin if Path(evo_bin).exists() and os.access(evo_bin, os.X_OK) else None
    )
    is_git = (ws / ".git").exists() or _git_toplevel(ws) is not None
    evo_dir = ws / ".evo"
    initialized = evo_dir.is_dir()
    # A baseline exists if the graph has at least one committed node. We avoid
    # importing evo internals; presence of a graph.json with a committed status
    # is a good-enough heuristic, and the real check happens in verify().
    has_baseline = False
    notes: list[str] = []
    if initialized:
        has_baseline = _graph_has_committed(evo_dir)
    return Detection(
        workspace=str(ws),
        evo_on_path=bool(found),
        evo_bin=found,
        is_git_repo=bool(is_git),
        evo_initialized=initialized,
        has_baseline=has_baseline,
        host=_detect_host(),
        notes=notes,
    )


def _git_toplevel(path: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def _graph_has_committed(evo_dir: Path) -> bool:
    """Scan any run_*/graph.json (or legacy graph.json) for a committed node."""
    candidates = list(evo_dir.glob("run_*/graph.json")) + [evo_dir / "graph.json"]
    for g in candidates:
        if not g.exists():
            continue
        try:
            data = json.loads(g.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        nodes = (data.get("nodes") or {})
        if isinstance(nodes, dict):
            nodes = nodes.values()
        for n in nodes:
            if isinstance(n, dict) and n.get("status") == "committed":
                return True
    return False


# --------------------------------------------------------------------------- #
# Phase 2: provision                                                          #
# --------------------------------------------------------------------------- #

def _installer_for_evo() -> list[str]:
    """Prefer `uv tool install` (evo's documented path); fall back to pip."""
    if shutil.which("uv"):
        return ["uv", "tool", "install", EVO_PIP_PACKAGE]
    return ["pip", "install", EVO_PIP_PACKAGE]


def provision(det: Detection, *, dry_run: bool = False) -> dict[str, Any]:
    """Install evo if missing. The bridge itself is already installed (we're
    running from it). Returns {ok, actions:[{cmd, ran, returncode, ...}], ...}.
    """
    actions: list[dict[str, Any]] = []
    if det.evo_on_path:
        return {"ok": True, "actions": [], "note": "evo already on PATH; nothing to install."}

    cmd = _installer_for_evo()
    action: dict[str, Any] = {"cmd": cmd, "purpose": f"install {EVO_PIP_PACKAGE}"}
    if dry_run:
        action["ran"] = False
        actions.append(action)
        return {"ok": True, "actions": actions, "dry_run": True}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        action["ran"] = True
        action["returncode"] = proc.returncode
        action["stdout_tail"] = proc.stdout.strip()[-1500:]
        if proc.returncode != 0:
            action["stderr_tail"] = proc.stderr.strip()[-1500:]
    except (OSError, subprocess.SubprocessError) as exc:
        action["ran"] = True
        action["error"] = str(exc)
        actions.append(action)
        return {"ok": False, "actions": actions,
                "reason": f"failed to install evo: {exc}"}
    actions.append(action)
    ok = action.get("returncode") == 0 and shutil.which("evo") is not None
    return {"ok": ok, "actions": actions,
            "reason": None if ok else "evo install command ran but `evo` is still not on PATH."}


# --------------------------------------------------------------------------- #
# Phase 3: init (scaffold only -- NOT discover, which spends compute)         #
# --------------------------------------------------------------------------- #

def initialize(det: Detection, *, dry_run: bool = False) -> dict[str, Any]:
    """Ensure the workspace scaffold exists (`evo init`). Does NOT run discover
    or commit a baseline -- that is the manual-start step, because it can run the
    benchmark and spend compute.
    """
    if det.evo_initialized:
        return {"ok": True, "ran": False, "note": ".evo/ already initialized."}
    if not det.is_git_repo:
        return {"ok": False, "ran": False,
                "reason": "workspace is not a git repository; evo needs a git repo. "
                          "Run `git init` (and an initial commit) first."}
    cmd = ["evo", "init"]
    if dry_run:
        return {"ok": True, "ran": False, "cmd": cmd, "dry_run": True}
    try:
        proc = subprocess.run(cmd, cwd=det.workspace, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "ran": True, "reason": f"`evo init` failed: {exc}"}
    return {
        "ok": proc.returncode == 0,
        "ran": True,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout.strip()[-1000:],
        "reason": None if proc.returncode == 0 else proc.stderr.strip()[-1000:],
    }


# --------------------------------------------------------------------------- #
# Phase 4: verify (the contract handshake -- "doctor")                        #
# --------------------------------------------------------------------------- #

# Fields the bridge relies on in each command's output. If evo renames one,
# doctor flags it here rather than letting a tool fail mid-loop.
_FRONTIER_KEYS = ("strategy", "nodes")
_NODE_KEYS = ("id",)  # score may be absent on a brand-new frontier


def doctor(cli: EvoCLI) -> dict[str, Any]:
    """Confirm evo responds and its JSON contracts match what the bridge parses.

    Safe to run any time; read-only. Returns a structured report; never raises.
    """
    report: dict[str, Any] = {
        "evo_bin": cli.evo_bin, "workspace": cli.workspace,
        "checks": [], "ok": True, "mismatches": [],
    }

    def _check(name: str, fn) -> None:
        entry: dict[str, Any] = {"name": name}
        try:
            fn(entry)
            entry.setdefault("ok", True)
        except EvoError as exc:
            entry["ok"] = False
            entry["error"] = str(exc)
            report["ok"] = False
        except Exception as exc:  # noqa: BLE001 - doctor must never raise
            entry["ok"] = False
            entry["error"] = f"unexpected: {exc}"
            report["ok"] = False
        report["checks"].append(entry)

    def _status(entry):
        txt = cli.status()
        entry["sample"] = txt.strip()[:120]

    def _frontier(entry):
        env = cli.frontier()
        missing = [k for k in _FRONTIER_KEYS if k not in env]
        if missing:
            entry["ok"] = False
            report["ok"] = False
            report["mismatches"].append(f"frontier envelope missing keys: {missing}")
        nodes = env.get("nodes") or []
        entry["node_count"] = len(nodes)
        if nodes:
            nmiss = [k for k in _NODE_KEYS if k not in nodes[0]]
            if nmiss:
                report["mismatches"].append(f"frontier node missing keys: {nmiss}")

    _check("evo status responds", _status)
    _check("evo frontier contract", _frontier)
    if report["mismatches"]:
        report["ok"] = False
    return report


# --------------------------------------------------------------------------- #
# Orchestration: bootstrap = detect -> provision -> init -> verify, then STOP  #
# --------------------------------------------------------------------------- #

def bootstrap(workspace: str, *, evo_bin: str = "evo",
              dry_run: bool = False) -> dict[str, Any]:
    """Run the full self-healing setup and return a structured result.

    Stops BEFORE the optimization loop. The `plan` field describes the
    manual-start the agent should present for the user's okay.
    """
    det = detect(workspace, evo_bin)
    result: dict[str, Any] = {"phase": "detect", "detection": asdict(det),
                              "dry_run": dry_run, "ready": False}

    prov = provision(det, dry_run=dry_run)
    result["provision"] = prov
    if not prov["ok"]:
        result["phase"] = "provision"
        result["blocker"] = prov.get("reason")
        return result

    # Re-detect after install so the rest sees the freshly-installed evo.
    if not det.evo_on_path and not dry_run:
        det = detect(workspace, evo_bin)
        result["detection"] = asdict(det)

    init = initialize(det, dry_run=dry_run)
    result["initialize"] = init
    if not init["ok"]:
        result["phase"] = "initialize"
        result["blocker"] = init.get("reason")
        return result

    # Verify only when we actually have a live evo (skip in dry_run / no-bin).
    if not dry_run and (det.evo_on_path or shutil.which(evo_bin)):
        cli = EvoCLI(workspace=det.workspace, evo_bin=(det.evo_bin or evo_bin))
        result["verify"] = doctor(cli)
    else:
        result["verify"] = {"skipped": True, "reason": "dry_run or evo not resolvable"}

    # Assemble the manual-start plan.
    needs_baseline = not det.has_baseline
    result["phase"] = "ready"
    result["ready"] = True
    result["plan"] = {
        "needs_discover": needs_baseline,
        "next": (
            "Propose metric + correctness gate from the repo and the user's goal, "
            "show the user, and on their okay run discover (commit a baseline) then "
            "start the optimize loop."
            if needs_baseline else
            "Baseline already committed. Propose the first round's parents from "
            "`evo_frontier`, show the user, and on their okay start the loop."
        ),
        "manual_start_gate": "Do not spend compute on discover/optimize until the user okays the plan.",
    }
    return result
