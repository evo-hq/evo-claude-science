# evo-mcp — a Claude Science ⇆ evo bridge

Drive an [evo](https://github.com/evo-hq/evo) autoresearch loop from any
MCP-capable host. evo is a durable, git-backed, **gated** tree-search
orchestrator for codebases (and any benchmarkable objective); this bridge
exposes its CLI as MCP tools so a generalist agent — Claude Science, Claude
Code, Cursor — can frame the problem, hand the durable search to evo, and
consume the results.

It is built so **others can use it too**: two independently installable pieces,
neither tied to a single host.

```
┌─────────────────┐   MCP (stdio)   ┌──────────────┐   subprocess   ┌──────────┐
│  any MCP host   │ ───────────────▶│  evo-mcp      │ ──────────────▶│ evo CLI   │
│ (Claude Science,│   evo_frontier  │  server       │  evo frontier  │ + .evo/   │
│  Claude Code…)  │   evo_new/run…  │ (this repo)   │  evo new/run…  │  graph    │
└─────────────────┘                 └──────────────┘                └──────────┘
```

## Why a bridge, not a competitor

evo and a generalist agent sit at **different layers**. evo is a *substrate*: a
persistent, gated, frontier-managed experiment graph that outlives any session.
A generalist agent is an *ephemeral reasoner*. They don't compete — the
generalist should **call** evo:

| Generalist (the caller) | evo (the engine) |
|---|---|
| Frame the objective under ambiguity | Durable git-backed experiment tree |
| Design the benchmark + gates | Gating: discard score-gaming experiments |
| Inject domain method-priors | Frontier selection over committed nodes |
| Render figures / write-up / PR from the digest | Unattended parallel hill-climb for hours |

## The two pieces

### 1. `evo-mcp` — the MCP server (this package)

```bash
pip install -e .            # or: uv tool install evo-mcp  (once published)
```

Add it to any MCP host's connector config, pointed at your repo:

```json
{
  "mcpServers": {
    "evo": {
      "command": "evo-mcp",
      "env": { "EVO_WORKSPACE": "/abs/path/to/your/repo" }
    }
  }
}
```

**That's the only setup a human does.** You do **not** pre-install evo, run
`evo init`, or learn evo's CLI. The first time the agent uses the connector it
calls `evo_bootstrap`, which **installs evo if missing, scaffolds the
workspace, and verifies the contracts** — idempotently. Then it stops and shows
you a plan (metric, gate, first move) for one okay before spending any compute.
This is the **auto-setup / manual-start** model.

You can also run the same setup from a shell:

```bash
EVO_WORKSPACE=/path/to/repo evo-mcp bootstrap     # detect→install→scaffold→verify
EVO_WORKSPACE=/path/to/repo evo-mcp doctor        # read-only contract handshake
EVO_WORKSPACE=/path/to/repo evo-mcp               # run the MCP server (stdio)
```

**Tools exposed** (a deliberate, loop-relevant subset of evo's CLI):

| Tool | Purpose |
|---|---|
| `evo_bootstrap` | **self-healing setup**: install evo, scaffold workspace, verify; stops before the loop |
| `evo_doctor` | read-only handshake: confirm evo responds + contracts match + skills located |
| `evo_skills` | list evo's OWN bundled driving-skills (the real protocols) as installed |
| `evo_skill` | read evo's REAL `SKILL.md` (optimize/subagent/discover/...) at runtime — **anti-drift** |
| `evo_status`, `evo_scratchpad` | orient: one-line + bounded state summary |
| `evo_frontier` | committed leaves ranked by the active strategy |
| `evo_show`, `evo_awaiting`, `evo_discards` | inspect nodes / pending decisions / history |
| `evo_new`, `evo_run` | branch an experiment, run benchmark + gates |
| `evo_prune`, `evo_discard` | trim the tree |
| `evo_ship` | distill the winner into a mergeable change |
| `evo_digest` | **composite** machine-readable run summary in one call |

Workspace file-ops (`evo bash/read/write/edit`) are **intentionally not
exposed** — editing experiment code is the inner agent's job, not the
orchestrator's, and a remote shell is not what a bridge should hand out.

### 2. `evo-operator` — the Claude Science skill (`skill/evo-operator/`)

The caller side. Publish it to the Claude Science skill catalog and another CS
user installs the loop in one step; it drives the server via
`host.mcp("evo", ...)`. See `skill/evo-operator/SKILL.md`.

## Anti-drift: the operator reads evo's REAL skills

evo's intelligence lives in the **driving-skills bundled inside the plugin**
(`discover`, `optimize`, `subagent`, `report`, `ship`, ...), not in the CLI.
When evo runs natively inside a host, the host loads those `SKILL.md` protocols.
A bridge that drives evo from outside could easily bypass that layer and rely on
a hand-written paraphrase — which silently drifts as evo evolves.

This bridge avoids that: `evo_skill(name)` locates the **installed** evo plugin
at runtime (via `evo.__file__` → plugin root → `skills/<name>/SKILL.md`, with an
`EVO_PLUGIN_ROOT` override) and returns the real protocol text. The operator
skill is instructed to read `optimize` before orchestrating and `subagent`
before writing briefs, and to treat the installed protocol as authoritative
whenever it disagrees with the bridge's own summary. So the operator always
follows the protocol of the evo version actually installed.

## The `evo_digest` endpoint

The bridge adds one thing evo's CLI doesn't have as a single call: a composite
digest (`frontier` + `status` + `awaiting`) assembled from evo's existing
read-only surfaces. This is the "expose the data, don't build the figure
engine" seam — the caller renders figures and the methods write-up *downstream*
from the digest, so evo never has to pretend to be a plotting tool.

## Standalone use (no MCP)

The wrapper layer has **zero MCP dependency** and is usable directly:

```python
from evo_mcp.cli import EvoCLI
cli = EvoCLI(workspace="/path/to/repo")
print(cli.frontier()["nodes"][0]["id"])
out = cli.run("exp_0021")          # {'outcome': 'committed', 'score': 0.86, ...}
```

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover tests -v
```

41 tests: 12 contract tests for `parse_run_line` against evo's real `evo run`
output lines, 9 integration tests that exercise `EvoCLI` end-to-end through a
stub `evo` executable (`tests/fixtures/fake_evo.py`) emitting evo's real JSON
envelopes, and 13 bootstrap/doctor tests (detection, idempotency, dry-run
provisioning, the not-a-git-repo blocker, and the contract handshake), and 7 skill-resolver tests (anti-drift: reading evo's real SKILL.md from a fake plugin tree). No real
evo install needed to test the bridge.

## Status & limitations

- The output contracts mirrored here were read from evo's source at evo 0.6.x.
  If evo changes a contract, the fixture tests are where the drift surfaces.
- A full *live* run needs a real `evo` install (`uv tool install evo-hq-cli`)
  plus a benchmarked workspace; this repo tests the bridge, not evo itself.

## License

Apache-2.0 (matches evo).
