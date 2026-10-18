"""
DEV ONLY. Exercises the whole pipeline offline with a fake model built from
the gold labeller, optionally corrupted, to test plumbing, voting and repair.

    python3 dev/mock_test.py [--noise 0.15] [--budget 1x]
"""
import argparse
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import client  # noqa: E402
import pipeline  # noqa: E402
import score  # noqa: E402
from dev.gold_labels import label_line  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--noise", type=float, default=0.0)
ap.add_argument("--budget", default="1x")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
rng = random.Random(a.seed)


def corrupt(rule):
    """Drop a rule, flip a direction, or invent one on a noise line."""
    if rule is None:
        return None
    m = re.match(r"(\w+)\((.*)\)", rule)
    op, args = m.group(1), [x.strip() for x in m.group(2).split(",")]
    if op in ("before", "right_before") and rng.random() < 0.5:
        return f"{op}({args[1]}, {args[0]})"
    return "noise"


def fake(item_id, messages, cap, max_tokens=1500):
    with client._lock:
        if client.CALLS[item_id] >= cap:
            return None
        client.CALLS[item_id] += 1
    user = messages[-1]["content"]
    if "Sentences" not in user:            # review call: confirm everything
        return "\n".join(f"L{n}: ok" for n in re.findall(r"^L(\d+):", user, re.M))
    out = []
    for n, text in re.findall(r"^L(\d+): (.*)$", user, re.M):
        rs = label_line(text)
        r = rs[0] if rs else None
        if a.noise and rng.random() < a.noise:
            r = corrupt(r) if r else None
        out.append(f"L{n}: {r or 'noise'}")
    return "\n".join(out)


pipeline.call_model = fake
items = json.load(open("items.json"))
key = json.load(open("visible_key.json"))
answers = {}
for it in items:
    answers[it["id"]], _ = pipeline.run_item(it, a.budget)
res = score.score(key, answers, items)
print(a.budget, "noise", a.noise, "macro", res["macro_exact_match"],
      res["per_case_rate"], "max calls", max(client.CALLS.values()))
