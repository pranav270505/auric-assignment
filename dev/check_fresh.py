"""
DEV ONLY. Sanity checks for a generated set (default dev/fresh):

  1. gold rules -> solver -> score.py must be 100% (key and text agree),
  2. the key scores itself 100% (scorer accepts every recorded citation set),
  3. no constraint wording is shared with items.json, the paraphrase set or
     the extraction prompt (no contamination),
  4. summary statistics next to the visible set's.

    python3 dev/check_fresh.py [dev/fresh]
"""
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import score  # noqa: E402
from extract import SYSTEM  # noqa: E402
from solver import body_lines, parse_header, parse_rule, solve  # noqa: E402

d = sys.argv[1] if len(sys.argv) > 1 else "dev/fresh"
items = json.load(open(os.path.join(d, "items.json")))
key = json.load(open(os.path.join(d, "key.json")))
gold = json.load(open(os.path.join(d, "gold_rules.json")))
ok = True

# 1. gold rules through the solver
ans = {}
for it in items:
    ro = parse_header(it["text"])
    rules = {int(i): [parse_rule(r, ro) for r in rs] for i, rs in gold[it["id"]].items()}
    assert all(all(x not in (None, "noise") for x in v) for v in rules.values()), it["id"]
    ans[it["id"]], _ = solve(it, rules)
r = score.score(key, ans, items)
print("1. gold rules -> solver:", r["macro_exact_match"], r["per_case_rate"])
ok &= r["macro_exact_match"] == 1.0

# 2. key scores itself (every recorded conflict set accepted by the scorer)
bad = 0
for it in items:
    k = key[it["id"]]
    for cs in k.get("conflict_line_sets", []):
        sub = {it["id"]: {"case": "inconsistent", "conflicts": cs}}
        one = score.score({it["id"]: k}, sub, [it])
        bad += one["macro_exact_match"] != 1.0
print("2. recorded conflict sets rejected by scorer:", bad)
ok &= bad == 0


# 3. wording overlap: 4-word shingles of constraint lines (names/times masked)
def shingles(s, names):
    s = s.lower()
    for n in names:
        s = re.sub(r"\b" + n.lower() + r"\b", "N", s)
    s = re.sub(r"\d\d:\d\d", "T", s)
    w = re.findall(r"[a-z']+|N|T", s)
    return {" ".join(w[i:i + 4]) for i in range(len(w) - 3)}


def constraint_shingles(item_list, gold_map=None):
    out = set()
    for it in item_list:
        ro = parse_header(it["text"])
        lines = body_lines(it["text"])
        idx = range(len(lines)) if gold_map is None else map(int, gold_map[it["id"]])
        for i in idx:
            out |= {s for s in shingles(lines[i], ro["people"]) if "N" in s or "T" in s}
    return out


fresh = constraint_shingles(items, gold)
others = {"items.json": constraint_shingles(json.load(open("items.json")))}
if os.path.exists("dev/paraphrase/items.json"):
    others["paraphrase set"] = constraint_shingles(json.load(open("dev/paraphrase/items.json")))
from extract import SYSTEM_SLOTS  # noqa: E402
PROMPT_NAMES = ["Bea", "Cy", "Dana", "Eli", "Alex", "Gus", "Hal", "Ivy", "Jo", "Kai",
                "X", "Y", "A", "B", "P"]
others["prompt (default)"] = shingles(SYSTEM, PROMPT_NAMES)
others["prompt (slots)"] = shingles(SYSTEM_SLOTS, PROMPT_NAMES)
if d != "dev/fresh" and os.path.exists("dev/fresh/items.json"):
    others["dev/fresh (pool A)"] = constraint_shingles(
        json.load(open("dev/fresh/items.json")), json.load(open("dev/fresh/gold_rules.json")))
for name, sh in others.items():
    common = fresh & sh
    print(f"3. shared 4-word patterns with {name}: {len(common)}",
          sorted(common)[:8] if common else "")

# 4. statistics
sols = Counter((k["case"], len(k["assignments"])) for k in key.values())
cores = [len(c) for k in key.values() for c in k["conflict_line_sets"]]
n_lines = [len(body_lines(it["text"])) for it in items]
n_rule = [len(gold[i]) for i in gold]
print("4. cases:", Counter(k["case"] for k in key.values()))
print("   ambiguous solution counts:", sorted(n for (c, n) in sols.elements() if c == "ambiguous"))
print(f"   lines per item {min(n_lines)}-{max(n_lines)}, constraint lines per item "
      f"{min(n_rule)}-{max(n_rule)} (mean {sum(n_rule) / len(n_rule):.1f})")
print(f"   minimal conflict sets: {len(cores)}, sizes {Counter(cores)}")
print("ALL CHECKS PASSED" if ok else "CHECKS FAILED")
