"""
DEV ONLY. Builds the paraphrase set: the same 60 puzzles with every
constraint sentence re-worded from a phrasing pool that appears nowhere in
items.json, and with hedges and restatement prefixes the visible set never
uses. The logic is untouched, so the key carries over; conflict citations are
mapped to the new sentences by line index.

None of these templates were shown to the model or used to tune the prompt.

    python3 dev/make_paraphrase_set.py   -> dev/paraphrase/items.json, key.json
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dev.gold_labels import label_line  # noqa: E402
from solver import body_lines, parse_header, parse_rule  # noqa: E402

rng = random.Random(7)


def term(t, poss=False):
    if t.startswith("@"):
        s = t[1:]
        base = rng.choice([f"whoever covers {s}", f"the {s} person",
                           f"the person running {s}"])
        return base + ("'s" if poss else "")
    return t + ("'s" if poss else "")


T = {
    "at": [lambda p, t: f"{p} is down for {t}.",
           lambda p, t: f"{t} has been given to {p}.",
           lambda p, t: f"{p} covers the {t} slot."],
    "not_at": [lambda p, t: f"{p} won't be working {t}.",
               lambda p, t: f"Don't expect {p} at {t}.",
               lambda p, t: f"{t} is off the table for {p}."],
    "on": [lambda p, s: f"{p} is running {s}.",
           lambda p, s: f"{s.capitalize()} falls to {p}.",
           lambda p, s: f"{p} looks after {s}."],
    "not_on": [lambda p, s: f"{p} isn't the one on {s}.",
               lambda p, s: f"{s.capitalize()} has gone to somebody other than {p}.",
               lambda p, s: f"{p} is kept off {s}."],
    "before": [lambda a, b: f"{term(a)} starts earlier than {term(b)}.",
               lambda a, b: f"{term(b)} is on at some point after {term(a)}.",
               lambda a, b: f"{term(a, True)} slot comes before {term(b, True)}."],
    "right_before": [lambda a, b: f"{term(b)} follows straight on from {term(a)}.",
                     lambda a, b: f"{term(a, True)} slot is immediately followed by {term(b, True)}.",
                     lambda a, b: f"{term(a)} is on, and {term(b)} has the very next block."],
    "between": [lambda x, a, b: f"{term(x)} is somewhere in between {term(a)} and {term(b)}.",
                lambda x, a, b: f"{term(x, True)} slot lies between those of {term(a)} and {term(b)}, whichever comes first.",
                lambda x, a, b: f"{term(a)} and {term(b)} bracket {term(x)} on the day."],
}
NEW_HEDGES = ["If memory serves, ", "Unless I have this wrong, ", "I believe ",
              "I'm reasonably confident ", "From what I recall, "]
NEW_RESTATE = ["Flagging this again: ", "Repeated for emphasis: "]


def render(rule, restated):
    op, *args = rule
    s = rng.choice(T[op])(*args)
    s = s[0].upper() + s[1:]
    if rng.random() < 0.4:
        h = rng.choice(NEW_HEDGES)
        s = h + (s[0].lower() + s[1:] if not s.split()[0] in ALLNAMES else s)
    if restated:
        s = rng.choice(NEW_RESTATE) + s
    return s


items = json.load(open("items.json"))
key = json.load(open("visible_key.json"))
ALLNAMES = {p for it in items for p in parse_header(it["text"])["people"]}
new_items, new_key = [], {}
for it in items:
    ro = parse_header(it["text"])
    head, lines = it["text"].split("\n")[0], body_lines(it["text"])
    mapping, out, seen = {}, [], set()
    for l in lines:
        rs = [parse_rule(r, ro) for r in label_line(l)]
        if rs:
            r = rs[0]
            new = render(r, restated=r in seen)
            seen.add(r)
        else:
            new = l
        mapping[l] = new
        out.append(new)
    new_items.append({**it, "text": head + "\n\n" + "\n".join(out)})
    k = dict(key[it["id"]])
    k["conflict_line_sets"] = [[mapping[c] for c in cs]
                               for cs in k.get("conflict_line_sets", [])]
    new_key[it["id"]] = k

os.makedirs("dev/paraphrase", exist_ok=True)
json.dump(new_items, open("dev/paraphrase/items.json", "w"), indent=1)
json.dump(new_key, open("dev/paraphrase/key.json", "w"), indent=1)
print("wrote", len(new_items), "items")
