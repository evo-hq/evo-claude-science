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

### 1. Fetch evo — codeload tarball, not git clone
`git clone` fails in the sandbox (`.git` creation blocked). Use the public
tarball (no credentials needed):
```bash
cd "$PWD"   # the CS workspace dir
curl -fsSL https://codeload.github.com/evo-hq/evo/tar.gz/refs/heads/main -o _evo.tgz
mkdir -p _evo_src && tar xzf _evo.tgz -C _evo_src --strip-components=1
ls _evo_src/plugins/evo/skills   # discover optimize subagent report ship finetuning infra-setup
```

### 2. Install the evo CLI
The `gitdir` backend (step 4) needs a build that ships it. Until it lands in a
PyPI release, install from the branch; switch to `pip install evo-hq-cli` once
released.
```bash
pip install -q "git+https://github.com/evo-hq/evo.git@gitdir-backend#subdirectory=plugins/evo" && evo --version
```

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

### 4. Configure the sandbox-safe git backend
evo's default `worktree` backend needs `.git`, which is blocked. Set the
`gitdir` backend as the workspace default:
```bash
evo config backend gitdir     # relocates GIT_DIR off the blocked .git name
```
The `gitdir` backend isolates each experiment with a relocated `GIT_DIR` and a
working tree that contains no `.git`, so evo's gated loop runs in the sandbox.
If `evo config backend gitdir` errors with an unknown backend, the installed
CLI predates the backend (step 2 installs from the branch that ships it) — say
so to the user instead of proceeding.

### 5. Verify and hand off
Confirm `evo --version` runs and the `evo-*` skills are in
`host.skills.list()`. Then STOP. Tell the user setup is complete and that to
begin they invoke `evo-discover` on the repo/pipeline they want to optimize —
that defines the metric + gate and commits a baseline, the first
compute-spending step, which waits for their okay.

## Notes
- Clean up `_evo.tgz` / `_evo_src` after publishing.
- The evo driving-skills are written in host dialect (they mention Bash,
  subagents, `/evo:` commands, hooks). Follow the *method*; map the *plumbing*
  to Claude Science's tools: kernel subprocess for `evo` calls, `host.delegate`
  for fan-out, `save_artifacts` for outputs.
- Re-run this skill to update evo (re-fetches latest, re-publishes).
