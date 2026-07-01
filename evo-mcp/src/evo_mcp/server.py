"""evo MCP server -- exposes the `evo` autoresearch CLI as MCP tools.

Any MCP-capable host (Claude Science, Claude Code, Cursor, ...) can point at
this server to drive an evo optimization loop programmatically. It is a thin,
read-mostly bridge: the durable git-backed experiment graph, gating, frontier
selection, and parallel execution all stay inside evo. This server only
translates MCP tool calls into `evo` subcommands and parses the results.

Run:
    EVO_WORKSPACE=/path/to/repo python -m evo_mcp.server
    # or pass --workspace; --evo-bin overrides the executable.

Transport: stdio (the MCP default for local servers).

Design notes
------------
* Tools are intentionally a SUBSET of evo's CLI -- the loop-relevant surface a
  generalist orchestrator needs: inspect (frontier/show/awaiting/status/
  scratchpad/digest), mutate the graph (new/run/prune/discard), and land
  (ship). Workspace file-ops (evo bash/read/write/edit) are deliberately NOT
  exposed: editing experiment code is the inner agent's job, not the
  orchestrator's.
* `evo_run` can be long. The server sets a generous default timeout and lets
  callers override; it never blocks the event loop because each call is
  dispatched to a worker thread.
* Every tool returns JSON-serializable dicts so the host renders structured
  results.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

_WIDGET_PATH = Path(__file__).parent / "dashboard_widget.html"


def _dashboard_html() -> str:
    """The self-contained MCP-UI dashboard widget (bundled next to this file)."""
    try:
        return _WIDGET_PATH.read_text(encoding="utf-8")
    except OSError:
        return "<!doctype html><meta charset=utf-8><p>evo dashboard widget missing.</p>"

from .cli import EvoCLI, EvoError
from . import bootstrap as _bootstrap
from . import skills as _skills

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - import-time guard
    raise SystemExit(
        "The `mcp` package is required to run the server. "
        "Install with `pip install mcp`. (The evo_mcp.cli wrapper layer has no "
        "such dependency and can be used standalone.)"
    ) from exc


def build_server(cli: EvoCLI) -> FastMCP:
    mcp = FastMCP("evo")

    # ---- setup (self-healing; safe to call first, before anything else) -- #

    @mcp.tool()
    def evo_bootstrap() -> dict[str, Any]:
        """Make the environment ready: install evo if missing, scaffold the
        workspace, and verify the contracts. Idempotent and safe to call first.

        STOPS before the optimization loop. The returned `plan` describes the
        manual-start the agent should present for the user's okay -- do not run
        discover or the loop until the user okays.
        """
        return _bootstrap.bootstrap(cli.workspace, evo_bin=cli.evo_bin)

    @mcp.tool()
    def evo_doctor() -> dict[str, Any]:
        """Read-only handshake: confirm evo responds and its JSON contracts
        match what this bridge parses. Use to debug a first run."""
        report = _bootstrap.doctor(cli)
        report["skills"] = _skills.list_skills(cli.evo_bin)
        return report

    @mcp.tool()
    def evo_skills() -> dict[str, Any]:
        """List evo's OWN bundled driving-skills (discover/optimize/subagent/
        report/ship/...) as actually installed, with their paths.

        These protocols -- not the CLI -- are evo's real intelligence. Use this
        to discover what driving-skills the installed evo version ships.
        """
        return _skills.list_skills(cli.evo_bin)

    @mcp.tool()
    def evo_skill(name: str, include_references: bool = False) -> dict[str, Any]:
        """Read evo's REAL SKILL.md for one driving-skill (e.g. "optimize",
        "subagent", "discover"), straight from the installed plugin.

        This is the anti-drift call: follow the protocol of the evo version
        actually installed, not a paraphrase baked into this bridge. Read
        `optimize` before orchestrating a loop and `subagent` before writing
        briefs. Set include_references=True to also pull the skill's
        references/ and workflows/ docs.
        """
        return _skills.read_skill(name, evo_bin=cli.evo_bin,
                                  include_references=include_references)

    # ---- inspect (read-only) --------------------------------------------- #

    @mcp.tool()
    def evo_status() -> str:
        """One-line workspace summary: metric, best score, experiment counts."""
        return cli.status()

    @mcp.tool()
    def evo_scratchpad() -> str:
        """Bounded state summary: tree, frontier, awaiting decisions, gates, notes."""
        return cli.scratchpad()

    @mcp.tool()
    def evo_frontier(strategy: str | None = None, seed: int | None = None) -> dict[str, Any]:
        """Frontier nodes (committed leaves) ranked by the configured (or overridden) strategy.

        Returns {strategy, nodes:[{id,score,rank,...}], generated_at, [seed]}.
        """
        return cli.frontier(strategy=strategy, seed=seed)

    @mcp.tool()
    def evo_show(exp_id: str) -> dict[str, Any]:
        """Full state of one experiment node (status, score, parent, children, attempts)."""
        return cli.show(exp_id)

    @mcp.tool()
    def evo_awaiting() -> list[dict[str, Any]]:
        """Evaluated nodes awaiting a commit/discard decision (cross-agent signal)."""
        return cli.awaiting()

    @mcp.tool()
    def evo_discards(like: str | None = None) -> str:
        """Discarded nodes, optionally filtered by a hypothesis substring (have-we-tried-this)."""
        return cli.discards(like=like)

    @mcp.tool()
    def evo_digest() -> dict[str, Any]:
        """Composite machine-readable run summary (frontier + status + awaiting) in one call.

        Intended for a downstream generalist that renders figures / a methods
        write-up without issuing several separate calls.
        """
        return cli.digest()

    # ---- mutate the graph ------------------------------------------------ #

    @mcp.tool()
    def evo_new(parent: str, hypothesis: str, from_artifact: str | None = None) -> dict[str, Any]:
        """Allocate a child experiment under `parent`. Returns its id + worktree path."""
        return cli.new(parent, hypothesis, from_artifact=from_artifact)

    @mcp.tool()
    def evo_run(exp_id: str, check: bool = False, staged_new_files: bool = False,
                timeout: float | None = None) -> dict[str, Any]:
        """Run (or --check) an experiment: benchmark + gates.

        Returns {outcome, exp_id, score, delta, detail, ok, returncode}. `outcome`
        is committed | evaluated | gate_failed | failed | check_passed. A
        non-committing outcome is a normal verdict, not a tool error.
        """
        return cli.run(exp_id, check=check, staged_new_files=staged_new_files, timeout=timeout)

    @mcp.tool()
    def evo_prune(exp_id: str, invalid: bool = False, reason: str = "", yes: bool = False) -> str:
        """Stop branching at a committed node (--exhausted), or mark it --invalid."""
        return cli.prune(exp_id, invalid=invalid, exhausted=not invalid, reason=reason, yes=yes)

    @mcp.tool()
    def evo_discard(exp_id: str, reason: str, failure_class: str | None = None) -> str:
        """Abandon a non-committed node. failure_class: build | eval | hypothesis."""
        return cli.discard(exp_id, reason=reason, failure_class=failure_class)

    # ---- land ------------------------------------------------------------ #

    @mcp.tool()
    def evo_ship(exp_id: str | None = None) -> str:
        """Distill the winning experiment into a mergeable change (PR or branch merge)."""
        return cli.ship(exp_id)

    # ---- live dashboard (MCP-UI app tile) -------------------------------- #

    @mcp.tool()
    def evo_get_graph() -> dict[str, Any]:
        """Live experiment graph for the dashboard: full node tree + frontier +
        one-line status. Backs the `ui://evo/dashboard` widget, which polls this
        on a timer. Reads `graph.json` directly, so it works mid-run."""
        out: dict[str, Any] = {"graph": cli.graph()}
        try:
            out["frontier"] = cli.frontier()
        except EvoError:
            pass
        try:
            out["status"] = cli.status().strip()
        except EvoError:
            pass
        return out

    @mcp.resource(
        "ui://evo/dashboard",
        name="evo dashboard",
        description="Live evo experiment tree, frontier, and scores. Self-refreshing.",
        mime_type="text/html;profile=mcp-app",
    )
    def evo_dashboard() -> str:
        """MCP-UI app tile: a self-refreshing dashboard rendered in a live iframe.
        The widget polls `evo_get_graph` over the host's tool relay, so it needs
        no bound port -- it works inside sandboxes that forbid TCP binds."""
        return _dashboard_html()

    return mcp


def main(argv: list[str] | None = None) -> int:
    import json as _json

    parser = argparse.ArgumentParser(
        description="evo MCP bridge. With no subcommand, runs the MCP server (stdio).")
    parser.add_argument("--workspace", default=os.environ.get("EVO_WORKSPACE", os.getcwd()),
                        help="evo workspace root (dir containing .evo/). Default: $EVO_WORKSPACE or cwd.")
    parser.add_argument("--evo-bin", default=os.environ.get("EVO_BIN", "evo"),
                        help="evo executable name or path. Default: $EVO_BIN or 'evo' on PATH.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the MCP server over stdio (default)")
    bp = sub.add_parser("bootstrap", help="self-healing setup: install evo, scaffold workspace, verify")
    bp.add_argument("--dry-run", action="store_true", help="show planned actions without executing")
    sub.add_parser("doctor", help="read-only contract handshake against the live evo")
    args = parser.parse_args(argv)

    cli = EvoCLI(workspace=args.workspace, evo_bin=args.evo_bin)

    if args.cmd == "bootstrap":
        result = _bootstrap.bootstrap(cli.workspace, evo_bin=cli.evo_bin,
                                      dry_run=getattr(args, "dry_run", False))
        print(_json.dumps(result, indent=2))
        return 0 if result.get("ready") else 1
    if args.cmd == "doctor":
        report = _bootstrap.doctor(cli)
        print(_json.dumps(report, indent=2))
        return 0 if report.get("ok") else 1

    # Default: run the server.
    server = build_server(cli)
    server.run()  # stdio transport
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
