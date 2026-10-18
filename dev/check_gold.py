"""
DEV ONLY. Perfect-extraction check: gold rules -> solver -> answers -> score.

    python3 dev/check_gold.py [--wishes-not-at]
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from solver import solve, parse_header, body_lines, parse_rule  # noqa: E402
from dev.gold_labels import label_line, unmatched_suspects  # noqa: E402
import score  # noqa: E402

wishes = "--wishes-not-at" in sys.argv
items = json.load(open("items.json"))
key = json.load(open("visible_key.json"))

answers, bad_rules, suspects = {}, [], []
for it in items:
    roster = parse_header(it["text"])
    lines = body_lines(it["text"])
    line_rules = {}
    for i, l in enumerate(lines):
        rs = []
        for r in label_line(l, wishes_as_not_at=wishes):
            p = parse_rule(r, roster)
            if p is None:
                bad_rules.append((it["id"], l, r))
            else:
                rs.append(p)
        if rs:
            line_rules[i] = rs
        if unmatched_suspects(l):
            suspects.append((it["id"], l))
    ans, diag = solve(it, line_rules)
    answers[it["id"]] = ans
    if ans["case"] != key[it["id"]]["case"] or diag["n_solutions"] > 4:
        print("MISMATCH", it["id"], key[it["id"]]["case"], "->", ans["case"], diag)

os.makedirs("dev/out", exist_ok=True)
out = "dev/out/gold_answers%s.json" % ("_wishes" if wishes else "")
json.dump(answers, open(out, "w"), indent=1)
for b in bad_rules:
    print("BAD RULE", b)
for s in suspects:
    print("SUSPECT NOISE", s)
res = score.score(key, answers, items)
res.pop("per_item")
print(json.dumps({k: res[k] for k in ("macro_exact_match", "per_case_rate",
                                      "confusion", "ambiguous_partial",
                                      "inconsistent_label_only")}, indent=1))
