"""
Symbolic core: header parsing, the rule language, brute-force solving,
case decision and minimal-conflict search.

No model calls here. The model's only job (see extract.py) is to map each
numbered body line to zero or more rules in the language below; everything
after that is deterministic.

Rule language (one rule per line of model output):
    at(P, T)            P works block T
    not_at(P, T)        P does not work block T
    on(P, S)            P holds station S
    not_on(P, S)        P does not hold station S
    before(A, B)        A's block is earlier than B's
    right_before(A, B)  A's block is immediately before B's
    between(X, A, B)    X's block is strictly between A's and B's (either order)
    noise               the line constrains nothing

A, B and X are terms: a person's name, or "@S" meaning "whoever holds station S".
"""
import itertools
import re

RULE_ARITY = {
    "at": 2, "not_at": 2, "on": 2, "not_on": 2,
    "before": 2, "right_before": 2, "between": 3,
}


# --------------------------------------------------------------------- header
def parse_header(text):
    """Roster facts from the header paragraph. Returns a dict or raises."""
    header = text.split("\n")[0]
    m = re.search(r"staff on the rota:\s*([^.]+)\.", header)
    people = _split_names(m.group(1))
    m = re.search(r"Blocks run\s+(.*?)(?:,\s*one person|\.)", header)
    blocks = re.findall(r"\d{1,2}:\d{2}", m.group(1))
    m = re.search(r"one person on each:\s*([^.]+)\.", header)
    stations = _split_names(m.group(1))
    m = re.search(r"people on a station are\s*([^;.]+)[;.]", header)
    holders = _split_names(m.group(1)) if m else list(people)
    return {"people": people, "blocks": blocks,
            "stations": stations, "holders": holders}


def _split_names(s):
    parts = re.split(r",\s*|\s+and\s+", s.strip())
    return [p.strip() for p in parts if p.strip()]


def body_lines(text):
    """Body statements, in order, exactly as written (the citation unit).

    Same split the scorer uses: every non-empty line after the header line.
    """
    return [l for l in text.split("\n")[1:] if l.strip()]


# ------------------------------------------------------------- rule language
RULE_RE = re.compile(r"^\s*([a-z_]+)\s*\((.*)\)\s*$")


def parse_rule(s, roster):
    """Parse one rule string. Returns a tuple, "noise", or None if invalid."""
    s = s.strip().strip("`").strip()
    if not s:
        return None
    if s.lower() in ("noise", "noise()", "none"):
        return "noise"
    m = RULE_RE.match(s)
    if not m:
        return None
    op = m.group(1)
    args = [a.strip().strip("'\"") for a in m.group(2).split(",")]
    if op not in RULE_ARITY or len(args) != RULE_ARITY[op]:
        return None
    if any("=" in a for a in args):
        args = _slot_order(op, args)
        if args is None:
            return None
    # Map back onto the roster's exact strings: the model's casing and block
    # formatting are not trusted, but the output must echo the notes verbatim.
    people = {p.lower(): p for p in roster["people"]}
    stations = {s.lower(): s for s in roster["stations"]}
    blocks = {_block_key(b): b for b in roster["blocks"]}

    def person(a):
        return people.get(a.lower())

    def station(a):
        return stations.get(a.lower().lstrip("@").strip())

    def term(a):
        if a.startswith("@"):
            st = station(a)
            return "@" + st if st else None
        return person(a)

    if op in ("at", "not_at"):
        p, b = person(args[0]), blocks.get(_block_key(args[1]))
        return (op, p, b) if p and b else None
    if op in ("on", "not_on"):
        p, s = person(args[0]), station(args[1])
        return (op, p, s) if p and s else None
    ts = [term(a) for a in args]
    if any(t is None for t in ts) or len(set(ts)) != len(ts):
        return None
    if op == "between":                      # A and B are unordered
        ts = [ts[0]] + sorted(ts[1:])
    return (op, *ts)


SLOT_ORDER = {"before": ("earlier", "later"), "right_before": ("earlier", "later")}


def _slot_order(op, args):
    """Named-slot arguments (the --ablate with_slots output format), e.g.
    before(later=Hana, earlier=Diego) -> [Diego, Hana]. The names decide the
    order, so a model that writes the later one first is still read right.
    between(middle=X, A, B) puts the middle first. Unnamed formats never
    reach here."""
    named, plain = {}, []
    for a in args:
        k, sep, v = a.partition("=")
        if sep:
            named[k.strip().lower()] = v.strip().strip("'\"")
        else:
            plain.append(a)
    if op in SLOT_ORDER:
        first, second = SLOT_ORDER[op]
        if set(named) != {first, second} or plain:
            return None
        return [named[first], named[second]]
    if op == "between":
        mid = named.pop("middle", None)
        rest = plain + list(named.values())
        return [mid] + rest if mid and len(rest) == 2 else None
    # at/on/not_at/not_on with a stray label (P=Ana): keep the values in order
    return [a.partition("=")[2].strip() if "=" in a else a for a in args]


def _block_key(b):
    m = re.match(r"^\s*(\d{1,2})[:.]?(\d{2})\s*$", b)
    return (int(m.group(1)), m.group(2)) if m else b.strip()


# --------------------------------------------------------------- the solver
def enumerate_schedules(roster):
    """Every schedule: (blocks per person as index, station per holder)."""
    people, blocks = roster["people"], roster["blocks"]
    holders, stations = roster["holders"], roster["stations"]
    out = []
    for bperm in itertools.permutations(range(len(blocks)), len(people)):
        bidx = dict(zip(people, bperm))
        for sperm in itertools.permutations(stations, len(holders)):
            out.append((bidx, dict(zip(holders, sperm))))
    return out


def holds(rule, sched, roster):
    bidx, st = sched
    op = rule[0]
    if op == "at":
        return roster["blocks"][bidx[rule[1]]] == rule[2]
    if op == "not_at":
        return roster["blocks"][bidx[rule[1]]] != rule[2]
    if op == "on":
        return st.get(rule[1]) == rule[2]
    if op == "not_on":
        return st.get(rule[1]) != rule[2]

    def t(x):
        if x.startswith("@"):
            who = next((p for p, s in st.items() if s == x[1:]), None)
            return None if who is None else bidx[who]
        return bidx[x]

    vals = [t(x) for x in rule[1:]]
    if any(v is None for v in vals):
        return False
    if op == "before":
        return vals[0] < vals[1]
    if op == "right_before":
        return vals[0] + 1 == vals[1]
    if op == "between":
        x, a, b = vals
        return min(a, b) < x < max(a, b)
    raise ValueError(op)


def line_masks(line_rules, scheds, roster):
    """Bitmask of satisfying schedules per line (AND of that line's rules)."""
    full = (1 << len(scheds)) - 1
    masks = {}
    for i, rules in line_rules.items():
        rules = [r for r in rules if r != "noise"]
        if not rules:
            continue
        m = full
        for r in rules:
            bits = 0
            for k, s in enumerate(scheds):
                if holds(r, s, roster):
                    bits |= 1 << k
            m &= bits
        masks[i] = m
    return masks, full


def smallest_conflict(masks, full, max_size=8):
    """A smallest set of lines whose rules cannot all hold.

    Searched in increasing size, so the first unsatisfiable set found has no
    unsatisfiable proper subset: it is a minimal conflicting set, and the
    smallest one, which keeps the citation as exposed to extraction error as
    little as possible.
    """
    idx = sorted(masks)
    for k in range(1, min(max_size, len(idx)) + 1):
        for combo in itertools.combinations(idx, k):
            m = full
            for i in combo:
                m &= masks[i]
                if not m:
                    break
            if not m:
                return list(combo)
    return None


def deletion_conflict(masks, full):
    """Fallback minimal conflicting set by deletion (for very large sets)."""
    core = sorted(masks)
    for i in list(core):
        rest = [j for j in core if j != i]
        m = full
        for j in rest:
            m &= masks[j]
        if not m:
            core = rest
    return core


def render(sched, roster):
    bidx, st = sched
    out = {}
    for p in roster["people"]:
        e = {"block": roster["blocks"][bidx[p]]}
        if p in st:
            e["station"] = st[p]
        out[p] = e
    return out


def solve(item, line_rules, max_ambiguous=4):
    """Decide one item from per-line rules. Returns (answer, diagnostics).

    line_rules: {line_index: [rule tuples or "noise"]}
    """
    roster = parse_header(item["text"])
    lines = body_lines(item["text"])
    scheds = enumerate_schedules(roster)
    masks, full = line_masks(line_rules, scheds, roster)
    m = full
    for v in masks.values():
        m &= v
    sols = [k for k in range(len(scheds)) if m >> k & 1]
    diag = {"n_solutions": len(sols), "n_rule_lines": len(masks)}
    if not sols:
        core = smallest_conflict(masks, full) or deletion_conflict(masks, full)
        diag["core"] = core
        return {"case": "inconsistent",
                "conflicts": [lines[i] for i in core]}, diag
    if len(sols) == 1:
        return {"case": "unique",
                "assignment": render(scheds[sols[0]], roster)}, diag
    # More than max_ambiguous solutions cannot be right (the brief caps it at
    # 4): a constraint was missed. We still answer ambiguous with the first
    # max_ambiguous so the output is well-formed; repair (when budget allows)
    # happens upstream, keyed off diag["n_solutions"].
    return {"case": "ambiguous",
            "assignments": [render(scheds[k], roster)
                            for k in sols[:max_ambiguous]]}, diag
