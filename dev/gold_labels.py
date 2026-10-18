"""
DEV ONLY -- never imported by the answering pipeline.

A regex labeller written against the *visible* set's phrasings. Its only job
is to produce gold per-line rules so that:
  1. the symbolic solver can be tested in isolation (perfect-extraction check),
  2. the model's extraction can later be scored line by line.

It is deliberately overfit to items.json and would break on the held-out
phrasing pool, which is exactly why it is not the extractor.
"""
import re

RESTATE_MAX = 60
HEDGES = [
    "i'm fairly sure ", "as far as i know, ", "speaking from memory, ",
    "going off the roster, ", "my recollection is that ",
]
# wishes / past / unresolved talk -- candidates for "noise"
WISH_RE = re.compile(
    r"^(?P<n>[A-Z][a-z]+) (lobbied for (?P<t1>\d\d:\d\d) without success"
    r"|wanted (?P<t2>\d\d:\d\d) and did not get it"
    r"|asked to move to the (?P<t3>\d\d:\d\d) block; the request was declined"
    r"|put in for the (?P<t4>\d\d:\d\d) block and was turned down)\.?$")
NOISE_HINTS = (
    "lobbied", "wanted", "asked to move", "put in for", "had taken",
    "Putting", "better fit", "Had the rota", "Nobody could remember",
    "disagreement", "question of", "nothing was minuted", "old arrangement",
    "In the spring", "Last month", "previous cycle",
)

E = r"(@[a-z]+|[A-Z][a-z]+)"
T = r"(\d\d:\d\d)"
S = r"([A-Za-z]+)"

# (pattern, builder taking match groups -> rule string)
PATTERNS = [
    # --- at
    (rf"Whoever drew the {T} block, it was {E}", lambda t, p: f"at({p}, {t})"),
    (rf"{E} is the one who opens up at {T}", lambda p, t: f"at({p}, {t})"),
    (rf"The {T} block is {E}'s", lambda t, p: f"at({p}, {t})"),
    (rf"{T} is when {E} is scheduled", lambda t, p: f"at({p}, {t})"),
    (rf"{E} takes {T}, as things stand", lambda p, t: f"at({p}, {t})"),
    (rf"{E} is on the {T} block", lambda p, t: f"at({p}, {t})"),
    # --- not_at
    (rf"{E} is unavailable at {T}", lambda p, t: f"not_at({p}, {t})"),
    (rf"{E} has a standing commitment that rules out the {T} block entirely",
     lambda p, t: f"not_at({p}, {t})"),
    (rf"You will not find {E} on the {T} block", lambda p, t: f"not_at({p}, {t})"),
    (rf"The {T} block is not {E}'s", lambda t, p: f"not_at({p}, {t})"),
    (rf"{T} is the one block {E} is definitely not on", lambda t, p: f"not_at({p}, {t})"),
    (rf"{E} is not on the {T} block", lambda p, t: f"not_at({p}, {t})"),
    # --- not_on (before on, so "is not on" wins)
    (rf"{E} is not assigned to {S}", lambda p, s: f"not_on({p}, {s.lower()})"),
    (rf"{E} is not on {S}", lambda p, s: f"not_on({p}, {s.lower()})"),
    (rf"{E} has not been put on {S}", lambda p, s: f"not_on({p}, {s.lower()})"),
    (rf"You can rule {E} out for {S}", lambda p, s: f"not_on({p}, {s.lower()})"),
    (rf"{S} is covered by someone other than {E}", lambda s, p: f"not_on({p}, {s.lower()})"),
    (rf"{S} is not {E}'s station", lambda s, p: f"not_on({p}, {s.lower()})"),
    # --- on
    (rf"{E} is on {S}", lambda p, s: f"on({p}, {s.lower()})"),
    (rf"{E} is assigned to {S}", lambda p, s: f"on({p}, {s.lower()})"),
    (rf"{E} has {S} this week", lambda p, s: f"on({p}, {s.lower()})"),
    (rf"{S} is {E}'s station", lambda s, p: f"on({p}, {s.lower()})"),
    (rf"{S} is covered by {E}", lambda s, p: f"on({p}, {s.lower()})"),
    (rf"The {S} station is down to {E}", lambda s, p: f"on({p}, {s.lower()})"),
    # --- before
    (rf"{E} works earlier in the day than {E}", lambda a, b: f"before({a}, {b})"),
    (rf"{E} is covered earlier in the day than {E}'s block", lambda a, b: f"before({a}, {b})"),
    (rf"{E} comes later in the day than {E}", lambda a, b: f"before({b}, {a})"),
    (rf"{E} is done before {E} starts", lambda a, b: f"before({a}, {b})"),
    (rf"{E}'s block falls somewhere earlier than {E}'s", lambda a, b: f"before({a}, {b})"),
    (rf"By the time {E} starts, {E} has already been on", lambda b, a: f"before({a}, {b})"),
    (rf"{E} takes over from {E} later in the day", lambda b, a: f"before({a}, {b})"),
    (rf"{E} is on later than {E}", lambda b, a: f"before({a}, {b})"),
    (rf"{E} precedes {E}", lambda a, b: f"before({a}, {b})"),
    (rf"{S} is covered before {E} comes on", lambda s, b: f"before(@{s.lower()}, {b})"),
    # --- right_before
    (rf"{E} is on the block directly after {E}", lambda b, a: f"right_before({a}, {b})"),
    (rf"{E} is on the block immediately before {E}", lambda a, b: f"right_before({a}, {b})"),
    (rf"{E} then {E}, back to back", lambda a, b: f"right_before({a}, {b})"),
    (rf"{E} hands straight over to {E}", lambda a, b: f"right_before({a}, {b})"),
    (rf"{E} relieves {E} directly, with no block in between", lambda b, a: f"right_before({a}, {b})"),
    (rf"There is no block between {E}'s and {E}'s, in that order", lambda a, b: f"right_before({a}, {b})"),
    # --- between
    (rf"Put {E} between {E} and {E}, though not necessarily next to either",
     lambda x, a, b: f"between({x}, {a}, {b})"),
    (rf"Whichever way round {E} and {E} are, {E} is between them",
     lambda a, b, x: f"between({x}, {a}, {b})"),
    (rf"{E}'s block falls between {E}'s and {E}'s, in one order or the other",
     lambda x, a, b: f"between({x}, {a}, {b})"),
    (rf"{E} sits between {E} and {E} on the rota", lambda x, a, b: f"between({x}, {a}, {b})"),
    (rf"{E} works at some point between {E} and {E}, not necessarily adjacent to either",
     lambda x, a, b: f"between({x}, {a}, {b})"),
    (rf"{E} is on after one of {E} and {E} and before the other",
     lambda x, a, b: f"between({x}, {a}, {b})"),
]
COMPILED = [(re.compile(p + r"\.?$"), f) for p, f in PATTERNS]

STATION_PHRASES = [
    (re.compile(r"\b(?:whoever has|whoever is on|the person on) (intake|calibration|packing)\b", re.I),
     lambda m: "@" + m.group(1).lower()),
    (re.compile(r"\bthe (intake|calibration|packing) station\b(?! is down to)", re.I),
     lambda m: "@" + m.group(1).lower()),
]


def normalise(line):
    s = line.strip()
    head, sep, tail = s.partition(": ")
    if sep and tail and len(head) < RESTATE_MAX:
        s = tail
    changed = True
    while changed:
        changed = False
        for h in HEDGES:
            if s.lower().startswith(h):
                s = s[len(h):]
                changed = True
    s = s[:1].upper() + s[1:]
    for rx, rep in STATION_PHRASES:
        s = rx.sub(rep, s)
    return s


def label_line(line, wishes_as_not_at=False):
    """Gold rule strings for one body line ([] means noise)."""
    s = normalise(line)
    if wishes_as_not_at:
        m = WISH_RE.match(s)
        if m:
            t = next(v for k, v in m.groupdict().items() if k != "n" and v)
            return [f"not_at({m.group('n')}, {t})"]
    if any(h in s for h in NOISE_HINTS):
        return []
    for rx, build in COMPILED:
        m = rx.match(s)
        if m:
            return [build(*m.groups())]
    return []


def unmatched_suspects(line):
    """Lines labelled noise that look like they might carry a constraint."""
    s = normalise(line)
    if label_line(line):
        return False
    if any(h in s for h in NOISE_HINTS):
        return False
    return bool(re.search(r"\d\d:\d\d|intake|calibration|packing|@|between|before|after|earlier|later", s, re.I))
