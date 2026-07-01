# evo × Claude Science

Run [evo](https://github.com/evo-hq/evo) autoresearch inside Claude Science.

evo is a durable, git-backed, gated tree-search that optimizes code (or any
benchmarkable objective). This repo ships the one-time setup skill a Claude
Science user installs so evo's optimization loop runs inside the CS workspace.

## Install

**Add this repo as a skill source in Claude Science** (`evo-hq/evo-claude-science`).
The importer picks up `skills/evo-autoresearch-install/SKILL.md`.

Or paste this into a Claude Science chat:

> Install the skill at
> `https://raw.githubusercontent.com/evo-hq/evo-claude-science/main/skills/evo-autoresearch-install/SKILL.md`
> — publish it via host.skills as `evo-autoresearch-install` and run it.

Either way the skill then fetches evo's driving-skills and publishes them into
your catalog, installs the evo CLI, and configures the `gitdir` backend so
evo's git layer works inside the sandbox. After setup, ask for an optimization
("tune X against metric Y") and evo's loop runs.

## Why a dedicated git backend

The Claude Science kernel sandbox blocks creating any path named `.git`, so
evo's default `worktree` engine can't run there. The `gitdir` backend (in evo
core) relocates git metadata off `.git` via `GIT_DIR`, so the gated loop runs
with the sandbox on.

## License

Apache-2.0 (matches evo).
