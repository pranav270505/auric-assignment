"""
DEV ONLY. Characterise the model from a run's logs: line-level extraction
accuracy against the gold labels, broken down by rule type and error kind,
plus the score of the run's answers.

    python3 dev/analyse.py runs/1x/logs runs/1x/answers.json [--examples 40]
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import score  # noqa: E402
from dev.gold_labels import label_line  # noqa: E402
from extract import parse_output  # noqa: E402
from solver import body_lines, parse_header, parse_rule  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("log_dir")
ap.add_argument("answers")
ap.add_argument("--examples", type=int, default=40)
a = ap.parse_args()

items = {it["id"]: it for it in json.load(open("items.json"))}
key = json.load(open("visible_key.json"))

by_type = defaultdict(Counter)       # gold op -> outcome counts
kinds = Counter()
examples = defaultdict(list)
parse_fail = Counter()

for fn in sorted(os.listdir(a.log_dir)):
    lg = json.load(open(os.path.join(a.log_dir, fn)))
    it = items[lg["id"]]
    roster = parse_header(it["text"])
    lines = body_lines(it["text"])
    gold = {i: frozenset(parse_rule(r, roster) for r in label_line(l))
            for i, l in enumerate(lines)}
    for s_idx, raw in enumerate(lg.get("raw", [])):
        parsed, stats = parse_output(raw, roster, len(lines))
        parse_fail["samples"] += 1
        parse_fail["missing_lines"] += len(lines) - stats["lines_seen"]
        parse_fail["invalid_rules"] += stats["invalid"]
        for i, l in enumerate(lines):
            g, p = gold[i], frozenset(parsed.get(i, []))
            gop = next(iter(g))[0] if g else "noise"
            if g == p:
                outcome = "correct"
            elif g and not p:
                outcome = "missed"
            elif not g and p:
                outcome = "spurious"
            else:
                outcome = "wrong"
            by_type[gop][outcome] += 1
            kinds[outcome] += 1
            if outcome != "correct" and len(examples[outcome]) < a.examples:
                examples[outcome].append(
                    (lg["id"], l, sorted(map(str, g)), sorted(map(str, p))))

print("=== extraction, per line, all samples ===")
tot = sum(kinds.values())
for k in ("correct", "missed", "spurious", "wrong"):
    print(f"  {k:9s} {kinds[k]:5d}  {kinds[k] / tot:.1%}")
print("  parse:", dict(parse_fail))
print("\n=== by gold rule type ===")
for op, c in sorted(by_type.items()):
    n = sum(c.values())
    print(f"  {op:13s} n={n:4d}  correct {c['correct'] / n:.0%}  "
          f"missed {c['missed'] / n:.0%}  spurious {c['spurious'] / n:.0%}  "
          f"wrong {c['wrong'] / n:.0%}")
for k, ex in examples.items():
    print(f"\n=== examples: {k} ===")
    for iid, l, g, p in ex[:a.examples]:
        print(f"  [{iid}] {l}\n      gold {g}\n      got  {p}")

answers = json.load(open(a.answers))
res = score.score(key, answers, list(items.values()))
res.pop("per_item")
print("\n=== score ===")
print(json.dumps({k: res[k] for k in ("macro_exact_match", "per_case_rate",
                                      "confusion", "ambiguous_partial",
                                      "inconsistent_label_only")}, indent=1))
