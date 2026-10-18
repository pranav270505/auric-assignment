"""
DEV ONLY. Prints a score table for one or more runs, straight to the terminal.

    python dev/compare.py SET:PATH[@LABEL] [SET:PATH[@LABEL] ...]

SET is visible, paraphrase, fresh, fresh2 or fresh_hard. PATH is either a run
directory (holding answers.json and, optionally, logs/) or an answers .json
file. With logs and gold rules available, the table also shows the per-line
error rate on clue lines and how often an ordered rule came out reversed.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import score  # noqa: E402
from extract import parse_output  # noqa: E402
from solver import body_lines, parse_header, parse_rule  # noqa: E402

SETS = {
    "visible": ("items.json", "visible_key.json", None),
    "paraphrase": ("dev/paraphrase/items.json", "dev/paraphrase/key.json", None),
    "fresh": ("dev/fresh/items.json", "dev/fresh/key.json", "dev/fresh/gold_rules.json"),
    "fresh2": ("dev/fresh2/items.json", "dev/fresh2/key.json", "dev/fresh2/gold_rules.json"),
    "fresh_hard": ("dev/fresh_hard/items.json", "dev/fresh_hard/key.json",
                   "dev/fresh_hard/gold_rules.json"),
}


def load(p):
    with open(p) as f:
        return json.load(f)


def gold_for(set_name, items):
    _, _, gp = SETS[set_name]
    if gp:
        g = load(gp)
        return {iid: {int(i): rs for i, rs in v.items()} for iid, v in g.items()}
    if set_name == "visible":
        from dev.gold_labels import label_line
        out = {}
        for it in items.values():
            ro = parse_header(it["text"])
            out[it["id"]] = {i: [l2 for l2 in label_line(l)]
                             for i, l in enumerate(body_lines(it["text"])) if label_line(l)}
        return out
    return None


def line_stats(log_dir, items, gold):
    clue = wrong = ordered = reversed_ = 0
    for fn in os.listdir(log_dir):
        lg = load(os.path.join(log_dir, fn))
        it = items[lg["id"]]
        ro, L = parse_header(it["text"]), body_lines(it["text"])
        g = {i: frozenset(parse_rule(r, ro) for r in rs) for i, rs in gold[lg["id"]].items()}
        for raw in lg.get("raw", []):
            if not raw:
                continue
            p = parse_output(raw, ro, len(L), sentences=L, reground=True)[0]
            for i, gg in g.items():
                q = frozenset(p.get(i, []))
                clue += 1
                wrong += q != gg
                r = next(iter(gg))
                if r[0] in ("before", "right_before"):
                    ordered += 1
                    reversed_ += q == frozenset([(r[0], r[2], r[1])])
    return clue, wrong, ordered, reversed_


def main():
    rows = []
    for arg in sys.argv[1:]:
        arg, _, custom = arg.partition("@")
        set_name, _, path = arg.partition(":")
        ip, kp, _ = SETS[set_name]
        items = {i["id"]: i for i in load(ip)}
        key = load(kp)
        ans_path = path if path.endswith(".json") else os.path.join(path, "answers.json")
        if not os.path.exists(ans_path):
            rows.append((set_name, os.path.basename(path.rstrip("/\\")), None))
            continue
        r = score.score(key, load(ans_path), list(items.values()))
        extra = ""
        log_dir = os.path.join(path, "logs")
        if os.path.isdir(log_dir):
            gold = gold_for(set_name, items)
            if gold:
                c, w, o, rv = line_stats(log_dir, items, gold)
                extra = f"{w / c:6.1%}  {rv / o:6.1%}" if c and o else ""
        label = custom or os.path.basename(os.path.dirname(ans_path) if not path.endswith(".json") else path)
        rows.append((set_name, label, (r, extra)))

    head = f"{'set':10s} {'run':28s} {'MACRO':>7s} {'uniq':>6s} {'ambig':>6s} {'incon':>6s}   {'clue-err':>8s} {'reversed':>8s}"
    print()
    print(head)
    print("-" * len(head))
    for set_name, label, val in rows:
        if val is None:
            print(f"{set_name:10s} {label:28s} {'(missing)':>7s}")
            continue
        r, extra = val
        pc = r["per_case_rate"]
        print(f"{set_name:10s} {label:28s} {r['macro_exact_match']:7.1%} {pc['unique']:6.0%} "
              f"{pc['ambiguous']:6.0%} {pc['inconsistent']:6.0%}   {extra}")
    print()
    print("MACRO = macro exact match (the headline). clue-err = share of clue lines read wrong;")
    print("reversed = share of ordered clues (before / right_before) read backwards.")


if __name__ == "__main__":
    main()
