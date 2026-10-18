"""
Model-based constraint extraction.

The model sees the roster and the body lines, numbered, with restatement
prefixes and hedge openers removed by code (they are texture, never content).
It answers with one rule per line in the language defined in solver.py.
It never quotes the notes: citations are rebuilt from line numbers by code.
"""
import re

from solver import parse_rule

# Restatement prefixes ("It bears repeating: ...") are split off generically on
# the first ": " in a short head, the same rule the scorer applies. Hedge
# openers are removed from a list that includes forms NOT seen in items.json
# (the scorer's own list hints at them); an unknown hedge just reaches the
# model, which is told hedges carry full force.
HEDGES = (
    "i'm fairly sure", "i am fairly sure", "as far as i know", "speaking from memory",
    "going off the roster", "my recollection is that", "from what i recall",
    "i believe", "if memory serves", "unless i have this wrong",
    "i'm reasonably confident", "i am reasonably confident", "i think",
    "if i remember rightly", "if i remember correctly", "to my knowledge",
    "i'm pretty sure", "i am pretty sure", "as i understand it", "apparently",
)


def clean_line(line, strip_hedges=True):
    s = " ".join(line.split())
    if not strip_hedges:
        return s
    head, sep, tail = s.partition(": ")
    if sep and tail and len(head) < 60 and not re.search(r"\d", head):
        s = tail
    changed = True
    while changed:
        changed = False
        low = s.lower()
        for h in HEDGES:
            if low.startswith(h):
                rest = s[len(h):].lstrip(" ,")
                if rest:
                    s = rest
                    changed = True
                break
    return s[:1].upper() + s[1:]


SYSTEM = """You translate shift-note sentences into formal scheduling rules. You do not solve anything.

THE RULE LANGUAGE (use exactly these forms):
  at(P, T)            person P works block T
  not_at(P, T)        person P does not work block T
  on(P, S)            person P is on station S
  not_on(P, S)        person P is not on station S
  before(A, B)        A's block is EARLIER than B's block (any gap)
  right_before(A, B)  A's block is IMMEDIATELY before B's block (no block between)
  between(X, A, B)    X's block is somewhere between A's and B's, in either order
  noise               the sentence says nothing definite about the CURRENT schedule

A, B and X are a person's name, or @S meaning "whoever is on station S"
(for example @packing = the person on packing).

HOW TO READ THE NOTES:
- Every hedge is irrelevant. "I think", "as far as I know", "fairly sure" etc. do not weaken anything. Translate the claim underneath.
- A repeated statement gets the same rule again.
- Wishes, requests, preferences, past or old arrangements, hypotheticals, and things that were discussed but not settled are noise. Examples of noise: someone wanted / asked for / lobbied for a block; someone would have been a better fit; if the rota had gone differently; last month or in the spring someone held a station; it was unclear or disputed whether someone had a block.
- A request that was refused or turned down is noise, even though the person did not get that block.
- Facts about keys, surveys, training, car-sharing, deliveries, parking etc. are noise.
- Instructions describe the rota as it is: "put X between A and B", "slot X after Y", "X goes on packing" are facts, not suggestions.
- "Not necessarily next to either" only means they need not be adjacent; it is not doubt. "As things stand", "this week", "for now", "has not been put on" describe the current rota.
- Watch the DIRECTION carefully. "Y comes after X", "Y is later than X", "Y takes over from X", "X is done before Y starts", "by the time Y starts X has been on" all mean before(X, Y).
- "Directly", "immediately", "back to back", "hands straight over", "relieves directly", "no block in between" mean right_before. Otherwise use before.
- "X is between A and B", "X is after one of A and B and before the other", "whichever order A and B are in, X is between them" all mean between(X, A, B), whether or not adjacency is mentioned.
- "Someone other than P is on S", "S is not P's station", "rule P out for S" mean not_on(P, S).
- "Whoever has S starts before P", "S is covered before P comes on", "the S station is covered earlier than P's block" mean before(@S, P).

EXAMPLES (names here are invented):
  "Bea's block is the one right after Cy's." -> right_before(Cy, Bea)
  "Dana finishes before Eli begins." -> before(Dana, Eli)
  "Eli is later in the day than whoever runs intake." -> before(@intake, Eli)
  "The 11:00 slot belongs to Alex." -> at(Alex, 11:00)
  "Cy can't do 07:00 at all." -> not_at(Cy, 07:00)
  "Cy is never on packing." -> not_on(Cy, packing)
  "Packing is handled by Dana." -> on(Dana, packing)
  "Whichever way round Alex and Bea fall, Eli sits between them." -> between(Eli, Alex, Bea)
  "Dana comes after one of Cy and Eli and before the other." -> between(Dana, Cy, Eli)
  "Slot Bea in between Cy and Eli; she need not be beside either." -> between(Bea, Cy, Eli)
  "Alex, then Bea, one straight after the other." -> right_before(Alex, Bea)
  "Eli has 09:00 for now." -> at(Eli, 09:00)
  "Cy hasn't been given intake." -> not_on(Cy, intake)
  "Alex was keen on 09:00 but it went to someone else." -> noise
  "Dana asked for 13:00 and was refused." -> noise
  "Two months ago Bea covered calibration." -> noise
  "Cy and Eli share a lift to work." -> noise

More direction examples:
  "Gus comes later in the day than Hal." -> before(Hal, Gus)
  "The person on calibration precedes Hal." -> before(@calibration, Hal)
  "Gus is on the block directly after Hal." -> right_before(Hal, Gus)
  "Gus takes over from Hal later in the day." -> before(Hal, Gus)

OUTPUT FORMAT: one line per numbered sentence, in the order given, and nothing else:
L1: <rule>
L2: <rule>
...
- No explanations, no comments, no notes after a rule, no second attempt. Answer each line once.
- A rule may only use people and stations named in THAT sentence (or @S for a station named in it).
- Use only names, blocks and stations from the roster. Use noise only for the kinds of sentence listed as noise above; every other sentence about who works when or where gets a rule."""


def _slotify(text):
    """Default prompt -> named-slot prompt: identical guidance, but every ordered
    relation is written with its slots named, so the model must decide which
    side is earlier instead of relying on argument position."""
    t = re.sub(r"\b(right_before|before)\(([^(),]+), ([^(),]+)\)",
               r"\1(earlier=\2, later=\3)", text)
    t = re.sub(r"\bbetween\(([^(),]+), ([^(),]+), ([^(),]+)\)",
               r"between(middle=\1, \2, \3)", t)
    return t


SLOT_NOTES = """

NAMED SLOTS (required):
- before and right_before always label both sides: earlier= is whoever's block comes first in the day, later= is whoever comes after. Work out who is earlier from the meaning of the sentence, not from which name is written first.
    "Jo starts only once Ivy has finished." -> before(earlier=Ivy, later=Jo)
    "Ivy is scheduled sooner than Jo." -> before(earlier=Ivy, later=Jo)
    "Jo's block is the very next one after Ivy's." -> right_before(earlier=Ivy, later=Jo)
    "Ivy is earlier than whoever runs packing." -> before(earlier=Ivy, later=@packing)
- between labels the one in the middle: between(middle=X, A, B).
    "Somewhere between Ivy and Kai you will find Jo." -> between(middle=Jo, Ivy, Kai)"""

SYSTEM_SLOTS = _slotify(SYSTEM).replace(
    "\n\nOUTPUT FORMAT:", SLOT_NOTES + "\n\nOUTPUT FORMAT:", 1)


def build_messages(roster, lines, strip_hedges=True, reverse=False, slots=False):
    """reverse=True lists the same numbered lines bottom-up: a second view of
    the item whose position-driven slips are not the same as the first's."""
    head = (f"Roster\n  people: {', '.join(roster['people'])}\n"
            f"  blocks: {', '.join(roster['blocks'])}\n"
            f"  stations: {', '.join(roster['stations'])}\n"
            f"  people on a station: {', '.join(roster['holders'])}\n")
    order = range(len(lines) - 1, -1, -1) if reverse else range(len(lines))
    body = "\n".join(f"L{i + 1}: {clean_line(lines[i], strip_hedges)}" for i in order)
    span = f"L{len(lines)} down to L1" if reverse else f"L1 to L{len(lines)}"
    user = f"{head}\nSentences\n{body}\n\nTranslate every sentence, {span}."
    return [{"role": "system", "content": SYSTEM_SLOTS if slots else SYSTEM},
            {"role": "user", "content": user}]


LINE_RE = re.compile(r"^\s*[-*]*\s*\**\s*L\s*(\d+)\s*\**\s*[:.)\-]\s*(.*?)\s*$", re.I)
RULE_CALL = re.compile(r"\b(at|not_at|on|not_on|before|right_before|between)\s*\(([^()]*)\)")
COMMENT = re.compile(r"\s*(//|#|\[|\(\s*[a-z]|\s—|\s--|\s—|note:|because)", re.I)


def parse_line_content(content, roster, robust=True):
    """Rules from the text after "L<n>:". Returns a list (maybe empty = noise)
    or None when nothing usable was said.

    Robust mode tolerates the model thinking aloud: trailing comments, a rule
    restated in an explanation, and station/block type slips."""
    c = content.strip().strip("`*").strip()
    if not robust:
        rules = []
        for part in re.split(r";|\s+&\s+", c):
            p = parse_rule(part.strip().rstrip("."), roster)
            if p is None:
                if part.strip():
                    return None
            elif p != "noise":
                rules.append(p)
        return rules
    if re.match(r"^noise\b", c, re.I):
        return []
    # the declared part comes before any comment; prefer rules found there
    cut = COMMENT.search(c, 1)
    head = c[:cut.start()] if cut else c
    for zone in (head, c):
        rules = []
        for m in RULE_CALL.finditer(zone):
            p = parse_rule(_fix_types(m.group(1), m.group(2), roster), roster)
            if p not in (None, "noise") and p not in rules:
                rules.append(p)
            if zone is c and rules:          # in free text, take the first valid one
                break
        if rules:
            return rules
    return None


def _fix_types(op, args, roster):
    """at/not_at with a station is an on/not_on slip, and vice versa."""
    parts = [a.strip() for a in args.split(",")]
    stations = {s.lower() for s in roster["stations"]}
    if op in ("at", "not_at") and len(parts) == 2 and parts[1].lower().lstrip("@") in stations:
        op = op.replace("at", "on")
    elif op in ("on", "not_on") and len(parts) == 2 and re.match(r"^\d{1,2}[:.]?\d{2}$", parts[1]):
        op = op.replace("on", "at")
    return f"{op}({', '.join(parts)})"


def grounded(rule, sentence):
    """Every person and station a rule mentions must appear in its sentence.
    Catches the model copying a name from a neighbouring line."""
    low = sentence.lower()
    for a in rule[1:]:
        if re.match(r"^\d", a):
            word = a
        else:
            word = a.lstrip("@").lower()
        if not re.search(r"(?<![a-z])" + re.escape(word.lower()) + r"(?![a-z])", low):
            return False
    return True


def _mentions(sentence, roster):
    """Roster entities named in a sentence, by kind."""
    low = sentence.lower()
    hit = lambda w: re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", low)
    return {"person": [p for p in roster["people"] if hit(p)],
            "station": [s for s in roster["stations"] if hit(s)],
            "block": [b for b in roster["blocks"] if hit(b)]}


def _kind(arg):
    if arg.startswith("@"):
        return "station"
    return "block" if re.match(r"^\d", arg) else "person"


def regrounded(rule, sentence, roster):
    """Repair a rule whose structure is right but one filler was copied from a
    neighbouring line: if exactly one argument is not named in the sentence,
    and the sentence names exactly one unused entity of a kind that fits that
    slot, substitute it. A block/station mix-up (not_at(P, 09:00) for a
    sentence that names a station) switches at<->on. Anything else: None."""
    if grounded(rule, sentence):
        return rule
    op, args = rule[0], list(rule[1:])
    bad = [k for k, a in enumerate(args) if not grounded((op, a), sentence)]
    if len(bad) != 1:
        return None
    k = bad[0]
    m = _mentions(sentence, roster)
    used = {a.lstrip("@") for a in args}
    kind = _kind(args[k])
    if op in ("at", "not_at", "on", "not_on") and k == 1:
        free_b = [b for b in m["block"] if b not in used]
        free_s = [s for s in m["station"] if s not in used]
        if len(free_b) + len(free_s) != 1:
            return None
        if free_b:
            return (op.replace("on", "at") if op.endswith("on") else op, args[0], free_b[0])
        return (op.replace("at", "on") if op.endswith("at") else op, args[0], free_s[0])
    pool = [p for p in m["person"] if p not in used] if kind == "person" else []
    if kind == "station" or (kind == "person" and not pool):
        pool_s = ["@" + s for s in m["station"] if s not in used]
        pool = pool if kind == "person" else pool_s
        if kind == "person" and not pool and op not in ("at", "not_at", "on", "not_on"):
            pool = pool_s
    if len(pool) != 1:
        return None
    args[k] = pool[0]
    if len(set(args)) != len(args):
        return None
    if op == "between":
        args = [args[0]] + sorted(args[1:])
    return (op, *args)


def parse_output(text, roster, n_lines, robust=True, sentences=None, reground=False):
    """{line_index: [rules]} plus stats. Missing or unusable lines are noise.

    When the model revisits a line (a draft, then a corrected list), the last
    usable answer for that line wins."""
    out, stats = {}, {"lines_seen": 0, "invalid": 0, "rules": 0}
    if not text:
        return out, stats
    for raw in text.splitlines():
        m = LINE_RE.match(raw)
        if not m:
            continue
        i = int(m.group(1)) - 1
        if not 0 <= i < n_lines:
            continue
        if not robust and i in out:
            continue
        rules = parse_line_content(m.group(2), roster, robust)
        if rules is None:
            stats["invalid"] += 1
            continue
        if sentences is not None:
            if reground:
                kept = [r2 for r in rules
                        if (r2 := regrounded(r, sentences[i], roster)) is not None]
            else:
                kept = [r for r in rules if grounded(r, sentences[i])]
            stats["ungrounded"] = stats.get("ungrounded", 0) + len(rules) - len(kept)
            rules = kept
        out[i] = rules
    stats["lines_seen"] = len(out)
    stats["rules"] = sum(len(v) for v in out.values())
    return out, stats
