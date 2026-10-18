#!/usr/bin/env python3
"""
Ablation table.

    python3 ablate.py live    [--budgets 1x,3x,10x] [--runs 1] [--items items.json]
    python3 ablate.py offline --logs DIR [--logs DIR ...]

live     Re-runs the full system once per (component off, budget) through the
         real endpoint and scores each run. This is the table to reproduce. Each
         cell is an independent sampled run, so it carries run-to-run noise
         (two 1x runs of the same system differed by 6.7 points on 60 items).

offline  Paired comparison with no model calls: takes saved run logs (made with
         --log-dir) and re-decides every item from the SAME model outputs with
         one post-processing component switched off at a time. Differences here
         are caused by the component alone, not by sampling. Only components
         that act after the model call can be ablated this way; hedge stripping
         changes the prompt, so it needs the live mode.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import score  # noqa: E402
from extract import parse_output  # noqa: E402
from pipeline import _core_flip, _rescue, _vote  # noqa: E402
from solver import body_lines, parse_header, solve  # noqa: E402

LIVE_ARMS = [
    ("full system", ""),
    ("- hedge stripping", "no_strip"),
    ("- robust parser", "no_robust_parse"),
    ("- grounding check (and re-grounding)", "no_ground"),
    ("- re-grounding only", "no_reground"),
    ("- voting (1 sample)", "no_vote"),
    ("- reversed-order samples", "no_diverse"),
    ("- rescue", "no_rescue"),
    ("+ self-check repair (removed)", "with_repair"),
    ("- core flip", "no_core_flip"),
    ("+ named slots (not adopted)", "with_slots"),
]
NEEDS_SAMPLES = {"no_vote", "no_diverse", "no_rescue", "with_repair"}


def score_file(items, key, path):
    r = score.score(key, json.load(open(path)), items)
    return r["macro_exact_match"], r["per_case_rate"]


def live(a):
    items = json.load(open(a.items))
    key = json.load(open(a.key))
    budgets = a.budgets.split(",")
    rows = []
    for name, flag in LIVE_ARMS:
        row = [name]
        for b in budgets:
            if b == "1x" and flag in NEEDS_SAMPLES:
                row.append("n/a")          # identical to full system at 1x
                continue
            scores = []
            for k in range(a.runs):
                out = os.path.join(a.out_dir, f"{flag or 'full'}_{b}_{k}.json")
                cmd = [sys.executable, os.path.join(HERE, "run.py"), a.items,
                       "--budget", b, "--out", out,
                       "--log-dir", out[:-len(".json")] + "_logs"]
                if flag:
                    cmd += ["--ablate", flag]
                with open(out[:-len(".json")] + "_console.txt", "w") as log:
                    subprocess.run(cmd, check=True, stdout=log, stderr=subprocess.STDOUT)
                scores.append(score_file(items, key, out)[0])
                print(f"  {name:40s} {b:>3s}  run {k + 1}: {scores[-1]:.1%}", flush=True)
            row.append(_cell(scores))
        rows.append(row)
        _print(budgets, rows)


def _cell(scores):
    m = sum(scores) / len(scores)
    if len(scores) == 1:
        return f"{m:.1%}"
    return f"{m:.1%} ({min(scores):.1%}-{max(scores):.1%}, n={len(scores)})"


def _print(budgets, rows):
    print("\n| Component | " + " | ".join(budgets) + " |")
    print("|---|" + "---|" * len(budgets))
    for r in rows:
        print("| " + " | ".join(r) + " |")
    sys.stdout.flush()


def offline(a):
    items = {it["id"]: it for it in json.load(open(a.items))}
    key = json.load(open(a.key))
    arms = [
        ("full system", {}),
        ("- robust parser", {"robust": False}),
        ("- grounding check (and re-grounding)", {"ground": False}),
        ("- re-grounding only", {"reground": False}),
        ("- voting (first sample only)", {"samples": 1}),
        ("- reversed-order samples", {"forward_only": True}),
        ("- rescue", {"rescue": False}),
        ("- core flip", {"core_flip": False}),
    ]
    for log_dir in a.logs:
        logs = [json.load(open(os.path.join(log_dir, f)))
                for f in sorted(os.listdir(log_dir))]
        n_raw = max(len(lg.get("raw", [])) for lg in logs)
        print(f"\n### {log_dir}  ({n_raw} saved sample(s) per item)")
        print("| Component | macro | unique | ambiguous | inconsistent |")
        print("|---|---|---|---|---|")
        for name, opt in arms:
            if n_raw == 1 and name.startswith(("- voting", "- reversed", "- rescue")):
                continue
            ans = {}
            for lg in logs:
                it = items[lg["id"]]
                ro, L = parse_header(it["text"]), body_lines(it["text"])
                raws = lg.get("raw", [])
                if opt.get("forward_only"):
                    # keep only forward-order samples; match the sample count
                    # of the full system by not topping up (fewer samples)
                    raws = raws[::2]
                raws = [r for r in raws if r is not None][:opt.get("samples")]
                ps = [parse_output(r, ro, len(L), robust=opt.get("robust", True),
                                   sentences=L if opt.get("ground", True) else None,
                                   reground=opt.get("reground", True))[0]
                      for r in raws]
                rules, _ = _vote(ps, len(L)) if ps else ({}, {})
                if opt.get("rescue", True) and len(ps) > 1:
                    rules, _ = _rescue(it, ps, rules)
                a1, d1 = solve(it, rules)
                if opt.get("core_flip", True) and d1["n_solutions"] == 0 and len(d1["core"]) < 3:
                    a1 = _core_flip(it, rules, a1, d1)[0]
                ans[lg["id"]] = a1
            r = score.score(key, ans, list(items.values()))
            pc = r["per_case_rate"]
            print(f"| {name} | {r['macro_exact_match']:.1%} | {pc['unique']:.0%} | "
                  f"{pc['ambiguous']:.0%} | {pc['inconsistent']:.0%} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["live", "offline"])
    ap.add_argument("--items", default=os.path.join(HERE, "items.json"))
    ap.add_argument("--key", default=os.path.join(HERE, "visible_key.json"))
    ap.add_argument("--budgets", default="1x,3x,10x")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--out-dir", default=os.path.join(HERE, "runs", "ablation"))
    ap.add_argument("--logs", action="append", default=[])
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    live(a) if a.mode == "live" else offline(a)


if __name__ == "__main__":
    main()
