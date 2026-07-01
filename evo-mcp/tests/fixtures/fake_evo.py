#!/usr/bin/env python3
"""A stub `evo` executable that emits evo's real output contracts.

Lets EvoCLI be integration-tested without a real evo install. Each subcommand
returns the JSON envelope / status line shape read from evo's source. Exit
codes mirror evo: `run` exits non-zero on EVALUATED/GATE_FAILED/FAILED.
"""
import json
import sys

args = sys.argv[1:]
cmd = args[0] if args else ""


def emit(obj):
    print(json.dumps(obj, indent=2))


if cmd == "frontier":
    emit({
        "strategy": {"kind": "pareto_per_task", "params": {"k": 5, "task_floor": 0.0}},
        "generated_at": "2026-06-30T12:00:00+00:00",
        "nodes": [
            {"id": "exp_0006", "score": 0.84, "rank": 1, "eval_epoch": 1,
             "hypothesis": "structured tool routing"},
            {"id": "exp_0002", "score": 0.78, "rank": 2, "eval_epoch": 1,
             "hypothesis": "route refund and cancel intents"},
        ],
        "seed": 12345,
    })
elif cmd == "new":
    emit({
        "id": "exp_0021",
        "parent": "exp_0006",
        "worktree": "/repo/.evo/run_0000/worktrees/exp_0021",
        "hypothesis": "add context pruning",
        "status": "active",
    })
elif cmd == "show":
    emit({
        "id": "exp_0006", "status": "committed", "score": 0.84,
        "parent": "exp_0002", "children": ["exp_0008", "exp_0005"],
        "hypothesis": "structured tool routing",
        "current_attempt": 1,
    })
elif cmd == "awaiting":
    emit([
        {"id": "exp_0014", "score": 0.39, "parent": "exp_0012",
         "reason": "gate_failed"},
    ])
elif cmd == "status":
    print("metric=max best=0.84 experiments=33 committed=11 frontier=5")
elif cmd == "scratchpad":
    print("TREE\n  root\n  └─ exp_0002 (0.78)\n     └─ exp_0006 ★ (0.84)\nFRONTIER\n  exp_0006  0.84")
elif cmd == "run":
    exp = next((a for a in args[1:] if a.startswith("exp_")), "exp_0021")
    if "--check" in args:
        print(f"CHECK_PASSED {exp} score=0.0 artifacts=/repo/.evo/check")
        sys.exit(0)
    # Default stub behavior: a committing improvement.
    print("preparing worktree...")
    print("running benchmark (task 1/3)")
    print(f"COMMITTED {exp} 0.86+0.02")
    sys.exit(0)
else:
    print(f"fake_evo: unknown command {cmd!r}", file=sys.stderr)
    sys.exit(2)
