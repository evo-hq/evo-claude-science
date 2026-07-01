---
name: evo-autoresearch-claude-science-setup
description: One-time setup that makes evo (github.com/evo-hq/evo) autoresearch runnable inside this Claude Science workspace. Use when the user wants to install or set up evo, run structured autoresearch, or optimize code / a config / a pipeline against a metric and wants evo's durable gated optimization loop available here. Fetches evo's driving-skills into the catalog, installs the evo CLI, and configures a sandbox-safe git backend. After this, invoke evo-discover on a target.
---

# evo autoresearch — Claude Science setup

Sets up [evo](https://github.com/evo-hq/evo) so its durable, git-backed, gated
optimization loop runs inside this Claude Science workspace. evo is the engine
(persistent experiment tree, gating, frontier search); Claude Science frames the
objective and consumes results. Run this once per workspace.

## The two constraints this works around

Claude Science's kernel is sandboxed. Two facts shape every step:

1. **Skills only enter the catalog via `host.skills.*`.** A raw file drop into
   the skills dir is ignored (the catalog is DB/backend-authoritative). So evo's
   skills are fetched, then published.
2. **The sandbox blocks any `.git` path** (creating `.git`, even as a file,
   fails). evo is git-native, so it must use its `gitdir` execution backend,
   which relocates git metadata off the `.git` name via `GIT_DIR`/`GIT_WORK_TREE`.
   This skill configures that backend as the default.

**Auto-setup / manual-start:** this installs and configures everything, then
STOPS. It never starts an optimization run (that spends compute) — it hands off
to `evo-discover`, which waits for the user's okay before the first run.

## Steps

Run bash/pip in the `python` tool (it has the workspace + network); run
`host.skills.*` in the `repl` control-plane tool. They share the workspace dir
but not memory, so stage files into the workspace first.

**Redirect evo's home into the workspace first.** evo keeps user-level state
(backend default, caches) in `~/.evo`, which the sandbox blocks. Point
`EVO_HOME` at a workspace-local dir before any `evo` call and keep it set for
every later call too (`evo install`, `evo-discover`, `evo run`, ...). In the
`python` kernel this persists for the session:
```python
import os
os.environ["EVO_HOME"] = os.path.join(os.getcwd(), ".evo_home")
```
(equivalently `export EVO_HOME="$PWD/.evo_home"` in bash). Without it, `evo
install` and any user-default read/write fail with `Operation not permitted:
~/.evo`.

### 1. Fetch evo — codeload tarball, not git clone
`git clone` fails in the sandbox (`.git` creation blocked). Use the public
tarball for the pinned release (no credentials needed); the tag must match the
CLI version in step 2 so `discover`'s version check agrees.
```bash
cd "$PWD"   # the CS workspace dir
curl -fsSL https://codeload.github.com/evo-hq/evo/tar.gz/refs/tags/v0.7.0-alpha.2 -o _evo.tgz
mkdir -p _evo_src && tar xzf _evo.tgz -C _evo_src --strip-components=1
ls _evo_src/plugins/evo/skills   # discover optimize subagent report ship finetuning infra-setup
```

### 2. Install the evo CLI into the shared python env (so child frames see it)
A session-scoped `pip install` lives only in the current kernel's ephemeral
overlay; delegated child frames run their own kernel and won't inherit it, so
evo's optimize loop (which fans out to child subagents) would hit `evo:
command not found`. Install the pinned release into the **shared/base python
environment** so every kernel inherits it:
```python
# python/repl tool — install durably; when prompted, target the shared base env
manage_packages(mode="install", packages=["evo-hq-cli==0.7.0a2"])
```
Then confirm `evo --version`; if you'll delegate, re-check `which evo` from a
fresh child frame. This release ships the `.git`-free `gitdir` backend (step 4),
and its workspace-local `EVO_HOME` fallback means child frames get a writable
evo home with no inherited env. (Bare `pip install "evo-hq-cli==0.7.0a2"` also
works but only in the current kernel — fine if you never delegate.)

### 3. Publish evo's driving-skills into the catalog
The `repl` kernel can't see granted host paths, only the workspace — the tarball
is already there from step 1. Publish each skill under an `evo-` prefix (evo's
names like `discover` are generic and would shadow other skills), rewriting the
frontmatter `name` to match. In a `repl` cell:
```python
import pathlib, re
base = pathlib.Path("_evo_src/plugins/evo/skills")
published = []
for d in sorted(p for p in base.iterdir() if (p / "SKILL.md").is_file()):
    new = "evo-" + d.name
    for f in (p for p in d.rglob("*") if p.is_file()):
        rel = f.relative_to(d).as_posix()
        txt = f.read_text(errors="ignore")
        if rel == "SKILL.md":
            txt = re.sub(r'(?m)^name:\s*.*$', f'name: {new}', txt, count=1)
        host.skills.edit(new, rel, txt)
    host.skills.publish(new); published.append(new)
print("published:", published)
```

### 4. Enable evo's sandbox git mode
evo's default `worktree` backend needs `.git`, which is blocked. Run the
Claude Science host install once; it sets the machine default so every new
workspace uses the `.git`-free `gitdir` backend:
```bash
evo install claude-science     # needs EVO_HOME from the preamble
```
A trailing `uv tool install` warning here is harmless: evo tries to self-sync
its CLI via `uv` (absent in the sandbox), but the backend default is already
written (confirm with `evo config backend show` → `gitdir`). The CLI came from
pip in step 2, so no sync is needed.

After this, when `evo-discover` runs `evo init`, it relocates the base repo off
`.git` (metadata under `.evo/basegit`, a baseline commit, no `.git` anywhere)
and every later `evo` command re-applies the relocated `GIT_DIR` automatically.
Nothing about the git workflow changes — same commits, diffs, branches, just a
renamed metadata dir. If `evo install claude-science` errors with an unknown
host, the installed CLI predates it (step 2 pins the release that ships it) —
say so instead of proceeding.

### 5. Verify and hand off
Confirm `evo --version` runs and the `evo-*` skills are in
`host.skills.list()`. Then STOP. Tell the user setup is complete and that to
begin they invoke `evo-discover` on the repo/pipeline they want to optimize —
that defines the metric + gate and commits a baseline, the first
compute-spending step, which waits for their okay.

## Running experiments on Modal / SSH (optional)
Base relocation and where experiments *execute* are independent. To keep the
git workflow local (relocated, sandbox-safe) but run the actual compute on the
user's Modal account or an SSH host, set a remote backend after discover:
```bash
evo config backend remote --provider modal    # or: --remote ssh:<host>
```
The local base stays `.git`-free; evo ships each experiment to the container
(which has normal `.git`) and harvests the result. No gitdir-specific change is
needed — it composes.

## Optional: live dashboard tile
For a live, self-refreshing dashboard (experiment tree, frontier, scores) as an
in-chat tile, add the `evo-mcp` connector. Two parts, and they split between
agent and human:

1. Install `evo-mcp` (the agent can do this, in the shared python env):
   ```bash
   pip install "git+https://github.com/evo-hq/evo-claude-science.git#subdirectory=evo-mcp"
   ```
   (or run it without installing: `uvx --from
   "git+https://github.com/evo-hq/evo-claude-science.git#subdirectory=evo-mcp" evo-mcp`.)
2. Add the connector (**human**, web UI — the agent cannot create a custom
   connector): Customize → Connectors → Add → **Local command**. Command:
   `evo-mcp`. Under Advanced settings, set env `EVO_WORKSPACE` to this project's
   workspace path (or leave it if CS spawns the connector in the workspace).

Once connected, `evo-mcp` exposes a `ui://evo/dashboard` MCP-App tile: a live
iframe that polls the run's `graph.json` every few seconds and renders the tree.
No port is bound, so it works inside the sandbox. It reads the same workspace
this skill set up; the search itself runs fine without it (use `evo status` /
`evo tree` otherwise).

## Notes
- Clean up `_evo.tgz` / `_evo_src` after publishing.
- The evo driving-skills are written in host dialect (they mention Bash,
  subagents, `/evo:` commands, hooks). Follow the *method*; map the *plumbing*
  to Claude Science's tools: kernel subprocess for `evo` calls, `host.delegate`
  for fan-out, `save_artifacts` for outputs.
- Re-run this skill to update evo (re-fetches latest, re-publishes).
