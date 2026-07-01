"""evo-mcp: an MCP bridge for the evo autoresearch CLI.

Public surface:
    from evo_mcp.cli import EvoCLI, EvoError, parse_run_line   # standalone, no MCP dep
    python -m evo_mcp.server --workspace /path/to/repo         # MCP server (needs `mcp`)
"""
from .cli import EvoCLI, EvoError, parse_run_line

__all__ = ["EvoCLI", "EvoError", "parse_run_line"]
__version__ = "0.1.0"
