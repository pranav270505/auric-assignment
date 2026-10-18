#!/usr/bin/env python3
"""
Entrypoint.

    ./run <items.json> --budget <1x|3x|10x> --out <answers.json>

Optional (development / ablation only; the defaults are the submitted system):
    --ablate NAME[,NAME]   switch components off: no_strip, no_vote,
                           no_diverse, no_rescue, with_repair (adds the
                           removed self-check repair back),
                           no_robust_parse, no_ground, no_reground,
                           no_core_flip, with_slots (named-slot output,
                           measured and not adopted)
    --log-dir DIR          per-item logs (raw model output, rules, repairs)
    --workers N            parallel items (default 8)
    --ids ID[,ID]          run a subset
"""
import argparse
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import CALLS  # noqa: E402
from pipeline import BUDGET_CAP, run_item  # noqa: E402
from solver import solve  # noqa: E402

ABLATIONS = {
    "no_strip": {"strip_hedges": False},
    "no_vote": {"vote": False},
    "no_diverse": {"diverse": False},
    "no_rescue": {"rescue": False},
    "with_repair": {"repair_unsat": True, "repair_wide": True},
    "no_robust_parse": {"robust_parse": False},
    "no_ground": {"ground": False, "reground": False},
    "no_reground": {"reground": False},
    "with_slots": {"slots": True},          # named-slot output (measured, not adopted)
    "no_core_flip": {"core_flip": False},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("items")
    ap.add_argument("--budget", required=True, choices=list(BUDGET_CAP))
    ap.add_argument("--out", required=True)
    ap.add_argument("--ablate", default="")
    ap.add_argument("--log-dir", default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--ids", default="")
    a = ap.parse_args()

    items = json.load(open(a.items))
    if a.ids:
        keep = set(a.ids.split(","))
        items = [it for it in items if it["id"] in keep]
    config = {}
    for name in filter(None, a.ablate.split(",")):
        config.update(ABLATIONS[name])

    answers, logs = {}, {}

    def one(it):
        try:
            return run_item(it, a.budget, config)
        except Exception:  # never lose an item to a crash
            traceback.print_exc()
            err = traceback.format_exc()
            if CALLS[it["id"]] == 0:
                # every item must reach the model at least once; retry with defaults
                try:
                    return run_item(it, a.budget, {})
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
            ans, diag = solve(it, {})
            return ans, {"id": it["id"], "error": err}

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(one, it): it["id"] for it in items}
        for k, f in enumerate(as_completed(futs), 1):
            iid = futs[f]
            answers[iid], logs[iid] = f.result()
            d = logs[iid].get("diag", {})
            print(f"[{k}/{len(items)}] {iid} -> {answers[iid]['case']} "
                  f"(solutions={d.get('n_solutions')}, calls={CALLS[iid]})",
                  flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump(answers, open(a.out, "w"), indent=1)
    if a.log_dir:
        os.makedirs(a.log_dir, exist_ok=True)
        for iid, lg in logs.items():
            lg["calls"] = CALLS[iid]
            json.dump(lg, open(os.path.join(a.log_dir, f"{iid}.json"), "w"), indent=1)

    cap = BUDGET_CAP[a.budget]
    bad = [i for i in answers if not 1 <= CALLS[i] <= cap]
    print(f"done: {len(answers)} items, calls/item min {min(CALLS[i] for i in answers)} "
          f"max {max(CALLS[i] for i in answers)}, budget violations: {bad or 'none'}")


if __name__ == "__main__":
    main()
