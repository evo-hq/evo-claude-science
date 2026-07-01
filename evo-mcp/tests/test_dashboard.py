"""Tests for the evo-mcp live dashboard (MCP-UI app tile).

Server side only (the widget's in-browser postMessage handshake is verified live
in Claude Science). Real files + the real FastMCP machinery, no mocks.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from evo_mcp.cli import EvoCLI
from evo_mcp.server import _dashboard_html, build_server


def _make_workspace(d: str) -> Path:
    root = Path(d)
    evo = root / ".evo"
    evo.mkdir()
    (evo / "meta.json").write_text(json.dumps({"active": "run_0000"}))
    run = evo / "run_0000"
    run.mkdir()
    (run / "graph.json").write_text(json.dumps({
        "metric": "max",
        "nodes": {
            "root": {"id": "root", "status": "root"},
            "exp_0001": {"id": "exp_0001", "status": "committed", "score": 0.5, "hypothesis": "try A"},
            "exp_0002": {"id": "exp_0002", "status": "evaluated", "score": 0.8, "hypothesis": "try B"},
        },
    }))
    return root


class TestGraphReader(unittest.TestCase):
    def test_reads_active_run_graph(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_workspace(d)
            g = EvoCLI(workspace=str(root)).graph()
            self.assertIn("exp_0002", g["nodes"])
            self.assertEqual(g["nodes"]["exp_0002"]["score"], 0.8)

    def test_missing_workspace_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(EvoCLI(workspace=d).graph(), {"nodes": {}})

    def test_falls_back_to_newest_run_without_meta(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            g1 = root / ".evo" / "run_0000"
            g1.mkdir(parents=True)
            (g1 / "graph.json").write_text(json.dumps({"nodes": {"a": {"id": "a"}}}))
            got = EvoCLI(workspace=str(root)).graph()
            self.assertIn("a", got["nodes"])


class TestDashboardWidget(unittest.TestCase):
    def test_widget_is_self_contained_mcp_ui(self):
        html = _dashboard_html()
        # No external scripts (must run inside the sandboxed iframe).
        self.assertNotIn("<script src", html)
        for marker in ("evo_get_graph", "tools/call", "ui/initialize", "postMessage"):
            self.assertIn(marker, html)


class TestDashboardResource(unittest.TestCase):
    def test_resource_registered_as_mcp_app(self):
        with tempfile.TemporaryDirectory() as d:
            mcp = build_server(EvoCLI(workspace=d))
            resources = asyncio.run(mcp.list_resources())
            by_uri = {str(r.uri): r for r in resources}
            self.assertIn("ui://evo/dashboard", by_uri)
            self.assertEqual(by_uri["ui://evo/dashboard"].mimeType, "text/html;profile=mcp-app")

    def test_resource_serves_the_widget(self):
        with tempfile.TemporaryDirectory() as d:
            mcp = build_server(EvoCLI(workspace=d))
            contents = list(asyncio.run(mcp.read_resource("ui://evo/dashboard")))
            text = "".join(str(c.content) for c in contents)
            self.assertIn("evo experiment dashboard", text)

    def test_get_graph_tool_returns_nodes(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_workspace(d)
            mcp = build_server(EvoCLI(workspace=str(root)))
            result = asyncio.run(mcp.call_tool("evo_get_graph", {}))
            # FastMCP returns (content_blocks, structured) or similar; find the graph.
            blob = json.dumps(result, default=str)
            self.assertIn("exp_0002", blob)


if __name__ == "__main__":
    unittest.main()
