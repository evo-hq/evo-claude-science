"""Surface evo's OWN bundled driving-skills at runtime, instead of paraphrasing them.

evo's real intelligence is not in the CLI -- it is in the skills shipped inside
the plugin (`discover`, `optimize`, `subagent`, `report`, `ship`, `finetuning`,
`infra-setup`, plus shared `references/`). When evo runs natively inside a host
(Claude Code, Cursor), the host loads those SKILL.md protocols and the inner
agent follows them.

A bridge that drives evo from OUTSIDE via the CLI bypasses that layer. If the
operator's knowledge of the loop is a hand-written paraphrase, it silently
DRIFTS from evo's actual protocol as evo evolves. The fix: locate the installed
evo plugin and read its real SKILL.md files at call time, so the operator always
follows the protocol of the evo version actually installed -- not a snapshot.

How the plugin root is found (mirrors evo's own dispatch.py path math):
    <plugin_root>/src/evo/__init__.py   ->  parents[2] == <plugin_root>
    <plugin_root>/skills/<name>/SKILL.md

Resolution order:
  1. $EVO_PLUGIN_ROOT, if set and contains a skills/ dir (explicit override).
  2. The installed `evo` package: import it, walk up from evo.__file__.
  3. A `skills/` dir next to the resolved evo CLI binary (editable installs).
Returns structured "not found" rather than raising, so the operator degrades to
its built-in summary with an explicit warning instead of crashing.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# evo's bundled skills, in rough loop order. `references` is the shared pool.
KNOWN_SKILLS = (
    "discover", "optimize", "subagent", "report", "ship",
    "finetuning", "infra-setup", "references",
)


def _candidate_roots(evo_bin: str | None = None) -> list[Path]:
    roots: list[Path] = []

    # 1. explicit override
    override = os.environ.get("EVO_PLUGIN_ROOT")
    if override:
        roots.append(Path(override))

    # 2. installed package: parents[2] of src/evo/__init__.py
    try:
        import importlib.util
        spec = importlib.util.find_spec("evo")
        if spec and spec.origin:
            pkg_init = Path(spec.origin).resolve()        # .../src/evo/__init__.py
            roots.append(pkg_init.parents[2])             # .../<plugin_root>
            roots.append(pkg_init.parents[1])             # fallback: .../src
            roots.append(pkg_init.parent)                 # fallback: .../evo
    except (ImportError, ValueError, IndexError):
        pass

    # 3. next to the evo binary (editable / tool installs sometimes symlink)
    if evo_bin:
        try:
            real = Path(evo_bin).resolve()
            for up in (real.parents[:4] if len(real.parents) >= 4 else real.parents):
                roots.append(up)
        except (OSError, IndexError):
            pass

    # de-dup preserving order
    seen: set[str] = set()
    uniq: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def find_skills_dir(evo_bin: str | None = None) -> Path | None:
    """Return the directory containing evo's bundled skills, or None."""
    for root in _candidate_roots(evo_bin):
        cand = root / "skills"
        if cand.is_dir() and any((cand / s / "SKILL.md").is_file() for s in KNOWN_SKILLS):
            return cand
        # some layouts nest under plugins/evo/skills
        nested = root / "plugins" / "evo" / "skills"
        if nested.is_dir() and (nested / "optimize" / "SKILL.md").is_file():
            return nested
    return None


def list_skills(evo_bin: str | None = None) -> dict[str, Any]:
    """List evo's bundled driving-skills with their resolved paths.

    Returns {ok, skills_dir, skills:[{name, has_skill_md, has_references,
    has_workflows, has_scripts}], reason?}.
    """
    sd = find_skills_dir(evo_bin)
    if sd is None:
        return {
            "ok": False,
            "skills_dir": None,
            "skills": [],
            "reason": (
                "Could not locate evo's bundled skills. Is evo installed? "
                "Set EVO_PLUGIN_ROOT to the plugin root (the dir containing "
                "skills/) to override. The operator will fall back to its "
                "built-in protocol summary, which may drift from your evo version."
            ),
        }
    skills: list[dict[str, Any]] = []
    for child in sorted(sd.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        skills.append({
            "name": child.name,
            "has_skill_md": skill_md.is_file(),
            "has_references": (child / "references").is_dir(),
            "has_workflows": (child / "workflows").is_dir(),
            "has_scripts": (child / "scripts").is_dir(),
        })
    return {"ok": True, "skills_dir": str(sd), "skills": skills}


def read_skill(name: str, evo_bin: str | None = None,
               include_references: bool = False,
               max_chars: int = 60_000) -> dict[str, Any]:
    """Read evo's REAL SKILL.md for `name` (and optionally its references/).

    This is the anti-drift call: the operator reads the protocol of the evo
    version actually installed, not a paraphrase baked into the bridge.

    Returns {ok, name, skill_md, [references], path, truncated, reason?}.
    """
    sd = find_skills_dir(evo_bin)
    if sd is None:
        return {"ok": False, "name": name, "reason": list_skills(evo_bin)["reason"]}

    skill_dir = sd / name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        avail = [p.name for p in sd.iterdir() if p.is_dir()]
        return {"ok": False, "name": name,
                "reason": f"no SKILL.md for {name!r}; available: {sorted(avail)}"}

    text = skill_md.read_text(encoding="utf-8")
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    out: dict[str, Any] = {
        "ok": True, "name": name, "path": str(skill_md),
        "skill_md": text, "truncated": truncated,
    }

    if include_references:
        refs: dict[str, str] = {}
        budget = max_chars
        for sub in ("references", "workflows"):
            d = skill_dir / sub
            if not d.is_dir():
                continue
            for f in sorted(d.rglob("*.md")):
                if budget <= 0:
                    out["references_truncated"] = True
                    break
                body = f.read_text(encoding="utf-8")[:budget]
                budget -= len(body)
                refs[str(f.relative_to(skill_dir))] = body
        if refs:
            out["references"] = refs
    return out
