"""
DEV ONLY. Full measurement run of the submitted system, in one command.

    python3 dev/final_run.py            resumable: finished runs are skipped
    python3 dev/final_run.py --redo     start over

Steps:
  1. unit tests (stops if any fail, before any model call is spent)
  2. live ablation on the visible set: every arm in ablate.LIVE_ARMS at 1x
     and 3x (arms that only act with several samples are skipped at 1x)
  3. full system on the visible set at 10x
  4. generalisation: full system and the named-slot variant on the
     paraphrase set, dev/fresh and dev/fresh2, at 1x and 3x

About 4,200 model calls. Prints one line per finished run, then the tables.
Run logs go to runs/final/; answers of the full system go to results/.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import score  # noqa: E402
from ablate import LIVE_ARMS, NEEDS_SAMPLES  # noqa: E402

OUT = os.path.join("runs", "final")
SETS = {
    "visible": ("items.json", "visible_key.json"),
    "paraphrase": ("dev/paraphrase/items.json", "dev/paraphrase/key.json"),
    "fresh": ("dev/fresh/items.json", "dev/fresh/key.json"),
    "fresh2": ("dev/fresh2/items.json", "dev/fresh2/key.json"),
}
VARIANT = ("with_slots", "named slots")
CALLS = {"1x": 1, "3x": 3, "10x": 10}


def load(p):
    with open(p) as f:
        return json.load(f)


def cell(set_name, budget, flag):
    return os.path.join(OUT, f"{set_name}_{flag or 'full'}_{budget}")


def run_cell(set_name, budget, flag, redo):
    d = cell(set_name, budget, flag)
    ans = os.path.join(d, "answers.json")
    if os.path.exists(ans) and not redo:
        return ans, True
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(d)
    cmd = [sys.executable, "run.py", SETS[set_name][0], "--budget", budget, "--out", ans,
           "--log-dir", os.path.join(d, "logs")]
    if flag:
        cmd += ["--ablate", flag]
    with open(os.path.join(d, "console.txt"), "w") as log:
        rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
    if rc != 0 or not os.path.exists(ans):
        print(f"  run failed: {set_name} {flag or 'full'} {budget} (see {d}/console.txt)")
        return None, False
    return ans, False


def result(set_name, ans):
    items_p, key_p = SETS[set_name]
    return score.score(load(key_p), load(ans), load(items_p))


def row(label, r):
    pc = r["per_case_rate"]
    return (f"{label:40s} {r['macro_exact_match']:7.1%} {pc['unique']:6.0%} "
            f"{pc['ambiguous']:6.0%} {pc['inconsistent']:6.0%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()

    print("1. Unit tests")
    t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       capture_output=True, text=True)
    print("   " + (t.stderr.strip().splitlines() or ["no output"])[-1])
    if t.returncode != 0:
        print(t.stderr)
        sys.exit("Unit tests failed. No model calls were made.")

    plan = [("visible", b, flag, name)
            for name, flag in LIVE_ARMS for b in ("1x", "3x")
            if not (b == "1x" and flag in NEEDS_SAMPLES)]
    plan.append(("visible", "10x", "", "full system"))
    for s in ("paraphrase", "fresh", "fresh2"):
        for b in ("1x", "3x"):
            plan += [(s, b, "", "full system"), (s, b, VARIANT[0], VARIANT[1])]
    calls = sum(CALLS[b] * len(load(SETS[s][0])) for s, b, _, _ in plan)
    print(f"2-4. {len(plan)} runs, about {calls} model calls")

    done, t0 = {}, time.time()
    for k, (s, b, flag, name) in enumerate(plan, 1):
        ans, skipped = run_cell(s, b, flag, a.redo)
        if not ans:
            continue
        r = result(s, ans)
        done[(s, b, flag)] = r
        note = "  (kept from earlier run)" if skipped else f"  [{(time.time() - t0) / 60:.0f} min]"
        print(f"  [{k:2d}/{len(plan)}] {s:10s} {b:>3s}  {name:38s} {r['macro_exact_match']:6.1%}{note}",
              flush=True)

    head = f"{'':40s} {'MACRO':>7s} {'uniq':>6s} {'ambig':>6s} {'incon':>6s}"
    print("\nAblation, visible set (live, one run per cell)")
    for b in ("1x", "3x", "10x"):
        print(f"\n{b}\n{head}")
        for name, flag in LIVE_ARMS:
            if (r := done.get(("visible", b, flag))):
                print(row(name, r))
    print("\nGeneralisation (full system vs named-slot variant)")
    for s in ("visible", "paraphrase", "fresh", "fresh2"):
        print(f"\n{s}\n{head}")
        for b in ("1x", "3x", "10x"):
            for flag, name in (("", "full system"), VARIANT):
                if (r := done.get((s, b, flag))):
                    print(row(f"{b}  {name}", r))

    os.makedirs("results", exist_ok=True)
    for (s, b, flag) in done:
        if not flag:
            dst = f"{s}_{b}.json"
            shutil.copy(os.path.join(cell(s, b, flag), "answers.json"), os.path.join("results", dst))
    print("\nFull-system answers written to results/. Run logs are in runs/final/.")


if __name__ == "__main__":
    main()
