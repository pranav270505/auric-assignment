"""
DEV ONLY. Generates a brand-new test set from scratch: new puzzles, new staff
names, and constraint / hedge / restatement / noise wordings that appear in
none of items.json, the paraphrase set, or the extraction prompt.

Unlike dev/make_paraphrase_set.py (same 60 puzzles, re-worded), every puzzle
here is new: a random true schedule, constraints sampled until the item is
unique / ambiguous (2-4 solutions) / inconsistent, and an answer key computed
by the solver, including EVERY minimal conflicting set, so any valid citation
scores. Inconsistent items are kept only if every minimal set has >= 3
statements, matching the brief.

    python3 dev/make_fresh_set.py [--profile visible|hard] [--n 60] [--seed 2026]

    --profile visible (default) -> dev/fresh       matched to the visible set
    --profile hard              -> dev/fresh_hard  the first, harsher set
    --pool B --seed 3031 --out dev/fresh2           second fresh set, new wording

Writes items.json and key.json (same formats as the visible set, so score.py
works unchanged) plus gold_rules.json (the true rule behind every line, for
line-level error analysis).
"""
import argparse
import itertools
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from solver import enumerate_schedules, holds, parse_header, render  # noqa: E402

NAMES = ["Arjun", "Chloe", "Diego", "Elif", "Farah", "Hana", "Ivo", "Kofi",
         "Lena", "Mateo", "Noor", "Oskar", "Rhea", "Sven", "Tara", "Wen"]
BLOCKS = ["07:00", "09:00", "11:00", "13:00", "15:00"]
STATIONS = ["intake", "calibration", "packing"]
BAYS = ["Bay 2", "Bay 5", "Bay 7", "Line C", "Hall 3"]

# ---------------------------------------------------------------- wordings
STATION_TERM = [
    (lambda s: f"the {s} lead", lambda s: f"the {s} lead's"),
    (lambda s: f"whoever staffs {s}", lambda s: f"the {s} person's"),
    (lambda s: f"the person stationed at {s}", lambda s: f"the {s} person's"),
]

T = {
    "at": [
        lambda p, t: f"{p} works the {t} shift.",
        lambda p, t: f"{p} has been rostered for {t}.",
        lambda p, t: f"The {t} shift is staffed by {p}.",
    ],
    "not_at": [
        lambda p, t: f"{p} cannot cover {t}.",
        lambda p, t: f"{t} is not an option for {p}.",
        lambda p, t: f"Leave {p} off the {t} shift.",
    ],
    "on": [
        lambda p, s: f"{p} is stationed at {s}.",
        lambda p, s: f"{p} staffs {s}.",
        lambda p, s: f"{s.capitalize()} is in {p}'s hands.",
    ],
    "not_on": [
        lambda p, s: f"{p} won't be anywhere near {s}.",
        lambda p, s: f"{s.capitalize()} goes to anyone but {p}.",
        lambda p, s: f"Keep {p} away from {s}.",
    ],
    "before": [
        lambda a, b: f"{a['n']} clocks in before {b['n']} does.",
        lambda a, b: f"{b['n']} arrives later than {a['n']}.",
        lambda a, b: f"{a['p']} shift is ahead of {b['p']}.",
    ],
    "right_before": [
        lambda a, b: f"{b['n']} takes over the moment {a['n']} finishes.",
        lambda a, b: f"{a['n']} and {b['n']} are consecutive, with {a['n']} first.",
        lambda a, b: f"Straight after {a['n']} comes {b['n']}.",
    ],
    "between": [
        lambda x, a, b: f"{x['n']} falls somewhere between {a['n']} and {b['n']}, in whichever order.",
        lambda x, a, b: f"{x['n']} is sandwiched between {a['n']} and {b['n']}, not necessarily touching either.",
        lambda x, a, b: f"With {a['n']} on one side and {b['n']} on the other, {x['n']} is somewhere in the middle.",
    ],
}
# Pool B: a second, independent wording pool (used for dev/fresh2). None of
# these phrasings appear in items.json, the paraphrase set, pool A, or either
# extraction prompt (default or the named-slot variant).
STATION_TERM_B = [
    (lambda s: f"the {s} operator", lambda s: f"the {s} operator's"),
    (lambda s: f"whoever handles {s}", lambda s: f"the {s} handler's"),
    (lambda s: f"the {s} station holder", lambda s: f"the {s} station holder's"),
]
T_B = {
    "at": [
        lambda p, t: f"{p} is on shift at {t}.",
        lambda p, t: f"{t} goes to {p}.",
        lambda p, t: f"{p}'s block is {t}.",
    ],
    "not_at": [
        lambda p, t: f"{p} is off at {t}.",
        lambda p, t: f"There is no {t} for {p}.",
        lambda p, t: f"{p} is not working {t}.",
    ],
    "on": [
        lambda p, s: f"{p} handles {s}.",
        lambda p, s: f"{s.capitalize()} belongs to {p} on this rota.",
        lambda p, s: f"{p} is the {s} operator.",
    ],
    "not_on": [
        lambda p, s: f"{p} is not handling {s}.",
        lambda p, s: f"{s.capitalize()} isn't {p}'s job.",
        lambda p, s: f"Someone besides {p} handles {s}.",
    ],
    "before": [
        lambda a, b: f"{a['n']} gets going earlier than {b['n']}.",
        lambda a, b: f"{b['n']} begins at some point after {a['n']}.",
        lambda a, b: f"{a['p']} turn comes before {b['p']}.",
    ],
    "right_before": [
        lambda a, b: f"{b['n']} begins exactly when {a['n']} ends.",
        lambda a, b: f"{a['n']} goes, and {b['n']} is on in the adjoining block.",
        lambda a, b: f"Nothing separates {a['p']} block from {b['p']}, which follows it.",
    ],
    "between": [
        lambda x, a, b: f"{x['n']} lands between {a['n']} and {b['n']}, whichever is first.",
        lambda x, a, b: f"{x['n']} is later than one of {a['n']} and {b['n']} and earlier than the other.",
        lambda x, a, b: f"{a['n']} and {b['n']} have {x['n']} somewhere between them.",
    ],
}
NEW_HEDGES = ["Correct me if I'm wrong, but ", "Last I checked, ",
              "To the best of my knowledge, ", "Pretty certain that ",
              "As I recall it, ", "If the board is right, "]
NEW_RESTATE = ["Said again for clarity: ", "Flagged twice at handover: ",
               "Once more: "]
DECOYS = [  # mention people / blocks / stations but state nothing current
    lambda p, t, s: f"{p} had hoped for {t}, but it went elsewhere.",
    lambda p, t, s: f"{p} asked about swapping into {t}; nothing came of it.",
    lambda p, t, s: f"Two rotas ago {p} ran {s}.",
    lambda p, t, s: f"Before the reshuffle, {p} usually took {t}.",
    lambda p, t, s: f"Had {p} been free, {t} would have suited everyone.",
    lambda p, t, s: f"Whether {p} should cover {s} is still being argued.",
    lambda p, t, s: f"Someone floated moving {p} to {t}; no decision yet.",
]
FILLER = [
    lambda p, q: "The kettle in the break room was finally replaced.",
    lambda p, q: f"{p} is organising the leaving card for the site manager.",
    lambda p, q: "A fire drill is pencilled in for next Thursday.",
    lambda p, q: f"{p} and {q} both mentioned the heating is uneven.",
    lambda p, q: "The forklift charger is back in service.",
    lambda p, q: f"{p} found the missing torque wrench.",
    lambda p, q: "Visitor badges are now kept at reception.",
    lambda p, q: f"{p} and {q} are doing the first-aid course together.",
    lambda p, q: "The loading bay door sticks in the cold.",
    lambda p, q: "Overtime forms go to the shift lead by Friday.",
]
# Two difficulty profiles.
#   visible (default): the visible set's measured profile -- rule mix, station
#     terms only as the first argument of before(), ~40% of clue lines hedged
#     with a plain opener, exactly one restatement, 7-11 clue lines, conflict
#     sets of 3-5 lines, ambiguous items mostly with 2 solutions. Hedges are
#     the five openers in score.py's list that items.json never uses (the
#     scorer was shipped to every candidate, so these are the most likely
#     held-out hedges); the extraction code strips them like any other.
#   hard: the first fresh set -- station terms anywhere, unseen hedges.
PROFILES = {
    "visible": {
        "weights": {"between": 28, "before@S": 17, "not_on": 13, "right_before": 10,
                    "not_at": 9, "at": 8, "before": 8, "on": 7},
        "station_terms": "first_of_before",
        "hedges": ["From what I recall, ", "I believe ", "If memory serves, ",
                   "Unless I have this wrong, ", "I'm reasonably confident "],
        "hedge_rate": 0.40, "restatements": [1], "base_rules": (6, 9),
        "core_sizes": (3, 5), "max_cores": 10,
        "ambiguous_targets": [2] * 12 + [3] * 2 + [4] * 6,
    },
    "hard": {
        "weights": {"before": 3, "before@S": 3, "between": 3, "right_before": 2,
                    "not_at": 2, "not_on": 2, "at": 1, "on": 1},
        "station_terms": "anywhere",
        "hedges": NEW_HEDGES, "hedge_rate": 0.35, "restatements": [1, 2],
        "base_rules": (4, 11), "core_sizes": (3, 6), "max_cores": 6,
        "ambiguous_targets": [4],
    },
}


def kind(r):
    if r[0] == "before" and r[1].startswith("@"):
        return "before@S"
    return r[0]


# ------------------------------------------------------------- generation
def atoms(roster, station_terms="anywhere"):
    """Every rule the language can state about this roster."""
    P, B, S, H = roster["people"], roster["blocks"], roster["stations"], roster["holders"]
    terms = P + ["@" + s for s in S]
    out = [("at", p, t) for p in P for t in B] + [("not_at", p, t) for p in P for t in B]
    out += [("on", h, s) for h in H for s in S] + [("not_on", h, s) for h in H for s in S]
    for a, b in itertools.permutations(terms, 2):
        if a.startswith("@") and b.startswith("@"):
            continue
        if station_terms == "first_of_before" and (b.startswith("@") or a.startswith("@")):
            if a.startswith("@") and not b.startswith("@"):
                out.append(("before", a, b))
            continue
        out += [("before", a, b), ("right_before", a, b)]
    for x, a, b in itertools.permutations(P, 3):
        if a < b:
            out.append(("between", x, a, b))
    return out


def mask_of(rule, scheds, roster):
    m = 0
    for k, s in enumerate(scheds):
        if holds(rule, s, roster):
            m |= 1 << k
    return m


def popcount(m):
    return bin(m).count("1")


def all_minimal_conflicts(masks, full):
    """Every minimal unsatisfiable subset of lines (indices into masks)."""
    idx = sorted(masks)
    found = []
    for k in range(1, len(idx) + 1):
        for combo in itertools.combinations(idx, k):
            if any(set(f) <= set(combo) for f in found):
                continue
            m = full
            for i in combo:
                m &= masks[i]
            if not m:
                found.append(combo)
    return found


def build_item(rng, case, prof):
    people = rng.sample(NAMES, 5)
    holders = rng.sample(people, 3)
    header = (f"Shift notes, {rng.choice(BAYS)}. 5 staff on the rota: {', '.join(people)}. "
              f"Blocks run {', '.join(BLOCKS)}, one person per block, and each person works "
              f"exactly one block. There are 3 stations, one person on each: "
              f"{', '.join(STATIONS)}. The people on a station are {', '.join(holders)}; "
              f"the rest are on no station.")
    roster = parse_header(header)
    scheds = enumerate_schedules(roster)
    full = (1 << len(scheds)) - 1
    truth = rng.randrange(len(scheds))
    cand = atoms(roster, prof["station_terms"])
    masks = {r: mask_of(r, scheds, roster) for r in cand}
    true_rules = [r for r in cand if masks[r] >> truth & 1 and masks[r] != full]
    W = prof["weights"]
    weighted = [r for r in true_rules for _ in range(W.get(kind(r), 0))]
    lo, hi = prof["base_rules"]

    chosen, cur = [], full
    tgt = rng.choice(prof["ambiguous_targets"]) if case == "ambiguous" else 1
    want = (lambda n: n == 1) if case != "ambiguous" else (lambda n: 2 <= n <= tgt)
    for _ in range(400):
        if want(popcount(cur)):
            break
        r = rng.choice(weighted)
        nxt = cur & masks[r]
        if r in chosen or nxt == cur:
            continue
        if case == "ambiguous" and popcount(nxt) < 2:
            continue
        chosen.append(r)
        cur = nxt
    if not want(popcount(cur)) or len(chosen) > hi:
        return None
    # pad with redundant true clues (they keep the solution set) up to the
    # profile's clue count, as the visible notes carry redundant clues too
    goal = rng.randint(max(lo, len(chosen)), hi)
    spare = [r for r in weighted if r not in chosen and masks[r] & cur == cur]
    rng.shuffle(spare)
    for r in spare:
        if len(chosen) >= goal:
            break
        if r not in chosen:
            chosen.append(r)

    if case == "inconsistent":
        false_rules = [r for r in cand if masks[r] and not (masks[r] & cur)]
        rng.shuffle(false_rules)
        for f in false_rules[:40]:
            trial = chosen + [f]
            ms = {i: masks[r] for i, r in enumerate(trial)}
            cores = all_minimal_conflicts(ms, full)
            c_lo, c_hi = prof["core_sizes"]
            if (cores and min(map(len, cores)) >= c_lo and max(map(len, cores)) <= c_hi
                    and len(cores) <= prof["max_cores"]):
                chosen = trial
                break
        else:
            return None

    # lines: constraints (some hedged), 1-2 restatements, decoys, filler
    lines = []                                  # (text, rule or None)
    used_tpl = {}
    for r in chosen:
        k = rng.randrange(3)
        used_tpl[r] = k
        lines.append((render_rule(rng, r, k, prof, hedge=rng.random() < prof["hedge_rate"]), r))
    for r in rng.sample(chosen, min(len(chosen), rng.choice(prof["restatements"]))):
        k = (used_tpl[r] + 1 + rng.randrange(2)) % 3   # always a different wording
        lines.append((rng.choice(NEW_RESTATE)
                      + render_rule(rng, r, k, prof, hedge=rng.random() < prof["hedge_rate"]), r))
    bidx, st = scheds[truth]
    for _ in range(rng.choice([3, 4])):
        p = rng.choice(people)
        t = rng.choice([b for b in BLOCKS if b != roster["blocks"][bidx[p]]])
        s = rng.choice([x for x in STATIONS if x != st.get(p)])
        lines.append((rng.choice(DECOYS)(p, t, s), None))
    target = rng.randint(17, 21)
    fill = rng.sample(FILLER, len(FILLER))
    while len(lines) < target and fill:
        p, q = rng.sample(people, 2)
        lines.append((fill.pop()(p, q), None))
    rng.shuffle(lines)

    text = header + "\n\n" + "\n".join(t for t, _ in lines)
    gold = {i: [fmt(r)] for i, (_, r) in enumerate(lines) if r}
    line_masks = {i: masks[r] for i, (_, r) in enumerate(lines) if r}
    m = full
    for v in line_masks.values():
        m &= v
    sols = [k for k in range(len(scheds)) if m >> k & 1]
    key = {"case": case, "assignments": [render(scheds[k], roster) for k in sols],
           "conflict_line_sets": []}
    if case == "inconsistent":
        key["conflict_line_sets"] = [[lines[i][0] for i in core]
                                     for core in all_minimal_conflicts(line_masks, full)]
    expected = {"unique": lambda n: n == 1, "ambiguous": lambda n: 2 <= n <= 4,
                "inconsistent": lambda n: n == 0}[case]
    if not expected(len(sols)):
        return None
    return text, key, gold


def render_rule(rng, r, k, prof, hedge):
    op, *args = r
    tpl = T_B if prof.get("pool") == "B" else T
    st_terms = STATION_TERM_B if prof.get("pool") == "B" else STATION_TERM
    if op in ("before", "right_before", "between"):
        def term(a):
            if a.startswith("@"):
                n, p = rng.choice(st_terms)
                return {"n": n(a[1:]), "p": p(a[1:])}
            return {"n": a, "p": a + "'s"}
        s = tpl[op][k](*[term(a) for a in args])
    else:
        s = tpl[op][k](*args)
    s = s[0].upper() + s[1:]
    if hedge:
        first = s.split()[0].rstrip("'s")
        if first not in NAMES and not first[0].isdigit():
            s = s[0].lower() + s[1:]
        s = rng.choice(prof["hedges"]) + s
    return s


def fmt(r):
    return f"{r[0]}({', '.join(r[1:])})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default=None)
    ap.add_argument("--profile", choices=list(PROFILES), default="visible")
    ap.add_argument("--pool", choices=["A", "B"], default="A",
                    help="constraint wording pool (B was written for dev/fresh2)")
    a = ap.parse_args()
    a.out = a.out or ("dev/fresh" if a.profile == "visible" else "dev/fresh_hard")
    prof = dict(PROFILES[a.profile], pool=a.pool)
    rng = random.Random(a.seed)
    cases = ["unique", "ambiguous", "inconsistent"] * (a.n // 3)
    rng.shuffle(cases)
    items, key, gold = [], {}, {}
    for n, case in enumerate(cases):
        while True:
            got = build_item(rng, case, prof)
            if got:
                break
        iid = f"F3-{n:03d}"
        text, k, g = got
        items.append({"id": iid, "text": text, "n_staff": 5, "n_stations": 3})
        key[iid], gold[iid] = k, g
    os.makedirs(a.out, exist_ok=True)
    json.dump(items, open(os.path.join(a.out, "items.json"), "w"), indent=1)
    json.dump(key, open(os.path.join(a.out, "key.json"), "w"), indent=1)
    json.dump(gold, open(os.path.join(a.out, "gold_rules.json"), "w"), indent=1)
    print(f"wrote {len(items)} items to {a.out}")


if __name__ == "__main__":
    main()
