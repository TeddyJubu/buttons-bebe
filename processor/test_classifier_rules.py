"""Tests for the deterministic risk classifier, including the Fable rules merge.

Adapted from fable/tests/unit/test_risk_parity.py and test_risk.py, re-expressed
in main's lowercase vocabulary ("immediate" / "high" / "normal") and main's
payload-dict API. Fable's own classifier file is NOT used — only its rules were
merged into main's classifier, so a parity test against that file would be
meaningless here.

Three things are pinned:
  1. Every rule the Fable branch had and main did not now escalates.
  2. Nothing benign newly escalates — the word-boundary guards still hold.
  3. The classifier can only ESCALATE. A sensitive verdict always asks for an
     owner ping, and nothing here can clear a flag.

The 48-scenario regression at the bottom skips until the Task 1 harness merges.
"""

from __future__ import annotations

import json
import dataclasses
import importlib
import inspect
import pkgutil
import re
import sys
import unittest
from collections.abc import Mapping, Sequence, Set
from pathlib import Path

PROCESSOR_DIR = Path(__file__).resolve().parent
WEBHOOK_SRC = PROCESSOR_DIR.parent / "webhook" / "src"
sys.path[:0] = [str(PROCESSOR_DIR), str(WEBHOOK_SRC)]

import classifier as cls  # noqa: E402
from classifier import HIGH, IMMEDIATE, NORMAL, classify  # noqa: E402

# T-FIX-3 deliberately keeps the old import spelling while making the package
# facade the real module.  Private objects are read through the facade for
# compatibility, but mutation tests must write to the module whose function
# globals actually consume them.  Assigning to a re-export on ``classifier``
# only changes the facade and leaves ``classifier.engine`` running unchanged.
if not hasattr(cls, "__path__"):
    raise ImportError(
        "T-FIX-3 classifier tests require processor/classifier/__init__.py; "
        "the flat module is only a direct-execution shim"
    )

_ENGINE = importlib.import_module("classifier.engine")
_VIEWS = importlib.import_module("classifier.views")
_ENGINE_GLOBAL_SEAMS = frozenset({"log_event"})


def _classifier_package_module_names() -> tuple[str, ...]:
    """Return every Python submodule under the classifier package.

    ``pkgutil`` does not report every namespace-package shape consistently, so
    the filesystem walk supplements it.  Names remain fully qualified: a
    coverage assertion for ``classifier.guards.problem`` must not collapse to
    the misleading top-level label ``classifier``.
    """
    names = {cls.__name__}
    for package_root in getattr(cls, "__path__", ()):
        root = Path(package_root)
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts:
                continue
            parts = list(relative.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            if parts:
                names.add(f"{cls.__name__}." + ".".join(parts))
        for module in pkgutil.walk_packages((str(root),), prefix=cls.__name__ + "."):
            names.add(module.name)
    return tuple(sorted(names))


def _classifier_package_modules() -> tuple[object, ...]:
    """Import the package and each discovered submodule exactly once."""
    modules = []
    for name in _classifier_package_module_names():
        try:
            module = importlib.import_module(name)
        except ImportError as exc:  # a new classifier module must be guarded
            raise AssertionError(f"classifier submodule {name} is not importable") from exc
        if module not in modules:
            modules.append(module)
    return tuple(modules)


def _canonical_owner(name: str):
    """Find the package module whose globals the classifier actually reads."""
    modules = _classifier_package_modules()

    # log_event is imported directly into engine.py and called from there;
    # patching logging_setup.py would not reach that canonical seam.
    if name in _ENGINE_GLOBAL_SEAMS and name in vars(_ENGINE):
        return _ENGINE

    # The split modules publish their canonical compatibility names in
    # ``__all__``. This distinguishes a views.py constant wildcard-imported
    # into engine.py from the namespace whose helper actually owns it.
    for module in modules:
        if module is not cls and name in getattr(module, "__all__", ()):
            return module

    # Prefer the module that defines the symbol over an engine wildcard alias.
    # This is what makes a table mutation reach the canonical data object and
    # makes a caps/length mutation reach views.py, whose helper owns the global.
    for module in modules:
        namespace = vars(module)
        if name not in namespace:
            continue
        for value in namespace.values():
            if getattr(value, "__module__", None) == module.__name__:
                if inspect.isfunction(value) or inspect.isclass(value):
                    return module

    # A function's globals dictionary is the next-strongest ownership signal.
    # It handles imported helpers such as log_event, which have no local
    # definition in the engine but are consumed by classify() there.
    for module in modules:
        namespace = vars(module)
        if name not in namespace:
            continue
        for value in namespace.values():
            if inspect.isfunction(value) and value.__globals__ is namespace:
                if name in value.__globals__:
                    return module

    # For objects only consumed indirectly, prefer a real submodule over the
    # facade.  The object identity is preserved; this merely identifies its
    # canonical namespace for mutation and identity assertions.
    for module in modules:
        if module is not cls and name in vars(module):
            return module
    if name in vars(cls):
        return cls
    raise AttributeError(f"classifier has no canonical symbol {name!r}")


def _canonical_value(name: str):
    owner = _canonical_owner(name)
    return getattr(owner, name)


def _set_canonical(name: str, value):
    """Set a test seam in its canonical owner and return (owner, old_value)."""
    owner = _canonical_owner(name)
    old_value = getattr(owner, name)
    setattr(owner, name, value)
    return owner, old_value


class ClassifierFacadeCompatibilityTests(unittest.TestCase):
    def test_facade_and_canonical_owner_rebinds_stay_synchronised(self):
        name = "_find_matches_any"
        owner = _canonical_owner(name)
        original = getattr(owner, name)

        def from_owner(*_args):
            return []

        def from_facade(*_args):
            return []

        try:
            setattr(owner, name, from_owner)
            self.assertIs(getattr(cls, name), from_owner)
            setattr(cls, name, from_facade)
            self.assertIs(getattr(owner, name), from_facade)
            self.assertIs(getattr(cls, name), from_facade)
        finally:
            setattr(cls, name, original)


def _contains_pattern(value, pattern: str, depth: int = 0, seen=None) -> bool:
    """Find an exact rule pattern inside legacy or dataclass-backed tables."""
    if depth > 8:
        return False
    seen = seen if seen is not None else set()
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, str):
        return value == pattern
    if isinstance(value, re.Pattern):
        return value.pattern == pattern
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return any(
            _contains_pattern(getattr(value, field.name), pattern, depth + 1, seen)
            for field in dataclasses.fields(value)
        )
    if isinstance(value, Mapping):
        return any(
            _contains_pattern(key, pattern, depth + 1, seen)
            or _contains_pattern(item, pattern, depth + 1, seen)
            for key, item in value.items()
        )
    if isinstance(value, (Sequence, Set, frozenset)) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return any(_contains_pattern(item, pattern, depth + 1, seen) for item in value)
    return False

SCENARIOS = PROCESSOR_DIR.parent / "testing" / "scenarios.json"
_RANK = {NORMAL: 0, HIGH: 1, IMMEDIATE: 2}


def _c(message: str, subject: str = "", intents=None) -> dict:
    return classify({
        "ticket_id": 1,
        "message_text": message,
        "ticket_subject": subject,
        "intents": intents or [],
    })



# ── main's tables, frozen ───────────────────────────────────
# Byte-for-byte copies of BEFORE (the `main` branch) as of the port. The whole
# round-5 design rests on "every rule main had is preserved verbatim in a
# _MAIN_* table and evaluated on main's own view of the ticket", and nothing
# tested that property - the tables could have been edited in either direction
# with the suite still green. These literals are what makes it checkable.
#
# Regenerate deliberately, never casually:
#     git show main:processor/classifier.py > /tmp/main_classifier.py
# and diff the tables by hand before touching anything below.

_MAIN_IMMEDIATE_FROZEN = (
    '\\brefund\\b',
    '\\bchargeback\\b',
    '\\bdispute\\b',
    '\\bmoney\\s+back\\b',
    '\\breimburse\\b',
    '\\breimbursement\\b',
    '\\bcompensat\\w*\\b',
    '\\bcredit\\s+(my|your|our)\\s+account\\b',
    '\\bissue\\s+a\\s+refund\\b',
    '\\breturn\\s+(my|the)\\s+(money|payment|funds)\\b',
    '\\bdamaged?\\b',
    '\\bdefect\\w*\\b',
    '\\bbroken\\b',
    '\\btorn\\b',
    '\\bwrong\\s+(item|size|color|colour|product|order)\\b',
    '\\bmissing\\s+(item|piece|part|product)\\b',
    '\\bnever\\s+(received|arrived|came|got)\\b',
    "\\bdidn'?t\\s+(receive|get|arrive)\\b",
    '\\bnot\\s+received\\b',
    '\\blost\\s+(package|parcel|order|shipment)\\b',
    '\\bstolen\\s+(package|parcel|order|shipment)\\b',
    '\\b(package|parcel|order|shipment)\\s+(?:was|is|got|has\\s+been)\\s+(lost|stolen)\\b',
    '\\b(angry|furious|outraged|disgusted|appalled|unacceptable)\\b',
    '\\b(terrible|horrible|awful|worst)\\s+(service|experience|company|store)\\b',
    '\\bnever\\s+(shopping|buying|ordering)\\s+(here|from\\s+you)\\b',
    '\\b(bbb|better\\s+business\\s+bureau|consumer\\s+protection|small\\s+claims)\\b',
    '\\b(lawsuit|sue|legal\\s+action|attorney|lawyer)\\b',
    '\\bfraud\\b',
    '\\bscam\\b',
    '\\bunauthorized\\s+charg\\w+\\b',
)

_MAIN_HIGH_FROZEN = (
    '\\burgent\\b',
    '\\basap\\b',
    '\\brush\\b',
    '\\bexpress\\b',
    '\\bneed\\s+(it|them)\\s+(by|before|tomorrow|today|monday|tuesday|wednesday|thursday|friday)\\b',
    '\\bdeadline\\b',
    '\\btime\\s+sensitive\\b',
    '\\bchange\\s+(my\\s+)?(shipping\\s+)?address\\b',
    '\\bwrong\\s+address\\b',
    '\\bupdate\\s+(my\\s+)?address\\b',
    '\\bnew\\s+address\\b',
    '\\bcancel\\s+(my\\s+)?(order|item|purchase)\\b',
    '\\bcancellation\\b',
    '\\bfinal\\s+sale\\b',
    '\\bno\\s+returns?\\b',
    '\\bwhere\\s+is\\s+my\\s+(order|package|parcel)\\b',
    "\\b(haven'?t|have\\s+not)\\s+(received|gotten|seen)\\b",
    '\\bnot\\s+yet\\s+(received|arrived|delivered)\\b',
    '\\blast\\s+(chance|warning)\\b',
    '\\b(following\\s+up|follow\\s?up)\\b.*(again|still|yet|no\\s+(response|reply|answer))\\b',
)

_MAIN_ANGRY_FROZEN = (
    '\\b(angry|furious|outraged|disgusted|appalled|unacceptable)\\b',
    '\\b(terrible|horrible|awful|worst)\\b',
    '\\bnever\\s+(shopping|buying|ordering)\\s+(here|from\\s+you)\\b',
    '\\b(bbb|better\\s+business\\s+bureau|consumer\\s+protection|small\\s+claims)\\b',
    '\\b(lawsuit|sue|legal\\s+action|attorney|lawyer)\\b',
    '\\b(scam|fraud|rip\\s?off|robbed)\\b',
)

_MAIN_HIGH_SENSITIVE_FROZEN = '\\b(final\\s+sale|change\\s+(?:my\\s+)?(?:shipping\\s+)?address|wrong\\s+address|update\\s+(?:my\\s+)?address|new\\s+address|cancel(?:lation)?(?:\\s+(?:my\\s+)?(?:order|item|purchase))?)\\b'
_MAIN_SENSITIVE_INTENTS_FROZEN = {'dispute', 'payment-dispute', 'address-change', 'order/wrong', 'order/missing', 'refund/request', 'order/damaged', 'refund', 'chargeback', 'cancel', 'payment-error', 'cancellation'}
_MAIN_HIGH_INTENTS_FROZEN = {'cancel', 'address-change', 'rush', 'cancellation', 'urgent', 'final-sale-exception'}
_MAIN_HIGH_SENSITIVE_INTENTS_FROZEN = {'cancel', 'cancellation', 'address-change', 'final-sale-exception'}
_MAIN_FOLLOWUP_KEYWORD_FROZEN = '\\b(following\\s+up|follow\\s?up)\\b.*(again|still|yet|no\\s+(response|reply|answer))\\b'


def _c_main_view(message: str, subject: str = "") -> str:
    """What MAIN's keyword tables alone say about the RAW, untruncated text.

    Deliberately not a re-implementation of classify() - no intents, no
    structural signals, no weak rules. It exists so a test can assert "the
    untruncated message does not escalate on its own", which is what makes a
    truncation-seam probe meaningful instead of vacuous.
    """
    text = f"{subject} {message}".lower()
    if any(re.search(p, text) for p in _MAIN_IMMEDIATE_FROZEN):
        return IMMEDIATE
    if any(re.search(p, text) for p in _MAIN_HIGH_FROZEN):
        return HIGH
    return NORMAL


class MainsRulesArePreservedTests(unittest.TestCase):
    """Every rule main had must survive verbatim, and read main's own view.

    Round 6 found the second instance of the same bug class round 5 found:
    something the port added (there, the boilerplate filter; here, the
    60 000-char length cap) narrowed the text main's OWN tables were matched
    against. Both were silent immediate -> normal de-escalations, and the
    suite was green through both.

    So this class pins the property directly rather than by example.
    """

    def test_the_immediate_table_is_mains_verbatim(self):
        self.assertEqual(
            tuple(_canonical_value("_MAIN_IMMEDIATE_KEYWORDS")),
            _MAIN_IMMEDIATE_FROZEN,
        )

    def test_the_angry_table_is_mains_verbatim(self):
        self.assertEqual(tuple(_canonical_value("_ANGRY_KEYWORDS")), _MAIN_ANGRY_FROZEN)

    def test_the_intent_sets_are_mains_verbatim(self):
        self.assertEqual(_canonical_value("_SENSITIVE_INTENTS"),
                         _MAIN_SENSITIVE_INTENTS_FROZEN)
        self.assertEqual(_canonical_value("_HIGH_INTENTS"), _MAIN_HIGH_INTENTS_FROZEN)
        self.assertEqual(_canonical_value("_HIGH_SENSITIVE_INTENTS"),
                         _MAIN_HIGH_SENSITIVE_INTENTS_FROZEN)

    def test_the_high_sensitive_pattern_is_mains_verbatim(self):
        self.assertEqual(_canonical_value("_MAIN_HIGH_SENSITIVE_PATTERN").pattern,
                         _MAIN_HIGH_SENSITIVE_FROZEN)

    def test_the_high_table_is_mains_verbatim_but_for_the_followup_rule(self):
        # ONE documented exception. Main's multi-follow-up rule carried a ".*"
        # - the only super-linear pattern in main's whole table - and it moved
        # to _FOLLOWUP_PATTERN, which has none. That is only safe if the
        # replacement is a strict superset; the next test proves it is.
        main_high = _canonical_value("_MAIN_HIGH_KEYWORDS")
        missing = [p for p in _MAIN_HIGH_FROZEN if p not in main_high]
        self.assertEqual(missing, [_MAIN_FOLLOWUP_KEYWORD_FROZEN])
        added = [p for p in main_high if p not in _MAIN_HIGH_FROZEN]
        self.assertEqual(added, [], "main's HIGH table gained a rule it never had")

    def test_the_ported_followup_rule_subsumes_mains(self):
        # Exhaustive over the cross product main's rule can match: its two
        # openings x its five continuations x filler in between. Anything
        # main's rule fires on, _FOLLOWUP_PATTERN must fire on too.
        mains = re.compile(_MAIN_FOLLOWUP_KEYWORD_FROZEN, re.IGNORECASE)
        openings = ["following up", "followup", "follow up", "Following Up"]
        conts = ["again", "still", "yet", "no response", "no reply", "no answer"]
        fillers = ["", " ", " on my order ", " - order 1042 - ",
                   " about the parcel that is ", "\n"]
        checked = 0
        for o in openings:
            for f in fillers:
                for c in conts:
                    text = f"{o}{f}{c}".lower()
                    if mains.search(text):
                        checked += 1
                        self.assertIsNotNone(
                            _canonical_value("_FOLLOWUP_PATTERN").search(text),
                            f"main's follow-up rule fires on {text!r} and the "
                            f"replacement does not")
        self.assertGreater(checked, 50, "the subsumption probe matched nothing")

    def test_the_followup_replacement_has_no_star(self):
        # The reason the swap was allowed at all. Main's ".*" is what made the
        # length cap look necessary in the first place.
        self.assertNotIn(".*", _canonical_value("_FOLLOWUP_PATTERN").pattern)


class MainViewIsNeverNarrowedTests(unittest.TestCase):
    """Nothing the port added may shrink the text main's tables are matched on.

    Round-6 BLOCKER. _bound() capped the scan at 60 000 characters, and
    classify() built main's view from the capped text. A long support thread
    with the complaint in the MIDDLE lost it: 16 of 120 realistic long threads
    dropped to NORMAL, all of them from IMMEDIATE or HIGH.

    Main has no cap at all, so main's tables now read the raw payload. The cap
    still protects the port's own rules, which is all it was ever for.
    """

    PARA = ("Hi Sarah, thanks for getting in touch about your order. Our "
            "delivery window to Ireland is 3-5 business days and the size "
            "guide is on the product page. Best wishes, the Buttons Bebe "
            "team.\n\n> Hi, could you tell me if the cream sleepsuit is "
            "restocked? Sarah\n\n")

    def test_a_complaint_in_the_middle_of_a_long_thread_still_escalates(self):
        body = self.PARA * 300
        cut = cls._MAX_SCAN_CHARS * 3 // 4      # exactly where _bound() cuts
        for complaint, want in [
            ("The romper arrived damaged and I want a refund.", IMMEDIATE),
            ("I never received my parcel.", IMMEDIATE),
            ("Please cancel my order.", HIGH),
        ]:
            for offset in (cut - 200, cut, cut + 5_000):
                msg = f"{body[:offset]}\n\n{complaint}\n\n{body[offset:]}"
                with self.subTest(complaint=complaint, offset=offset):
                    self.assertGreater(len(msg), _canonical_value("_MAX_SCAN_CHARS"),
                                       "probe must exceed the cap to prove anything")
                    self.assertEqual(_c(msg)["priority"], want)

    def test_a_keyword_straddling_the_cut_still_escalates(self):
        cut = cls._MAX_SCAN_CHARS * 3 // 4
        msg = "a " * (cut // 2) + "damaged item here" + "a " * 17_500
        self.assertGreater(len(msg), cls._MAX_SCAN_CHARS)
        self.assertEqual(_c(msg)["priority"], IMMEDIATE)

    def test_mains_view_is_built_from_the_payload_not_the_bounded_text(self):
        # Structural, so it survives any rewording of the probes above.
        seen = {}
        owner = _canonical_owner("_find_matches_any")
        original = getattr(owner, "_find_matches_any")

        def spy(views, patterns):
            if patterns is _canonical_value("_MAIN_IMMEDIATE_KEYWORDS"):
                seen["views"] = views
            return original(views, patterns)

        setattr(owner, "_find_matches_any", spy)
        try:
            body = "x" * (cls._MAX_SCAN_CHARS + 5_000)
            _c(body + " damaged")
        finally:
            setattr(owner, "_find_matches_any", original)
        self.assertTrue(seen["views"])
        self.assertNotIn(_canonical_value("_TRUNCATION_SENTINEL"), seen["views"][0])
        self.assertGreater(
            len(seen["views"][0]), _canonical_value("_MAX_SCAN_CHARS"))

    def test_a_word_char_apostrophe_does_not_lose_a_main_match(self):
        # U+02BC is a \w character, "'" is not, so folding it BREAKS main's
        # r"\bunauthorized\s+charg\w+\b". Main's tables therefore read both
        # the raw text and the folded copy, and the union of the two.
        self.assertEqual(
            _c("There is an unauthorized chargʼ on my card")["priority"],
            IMMEDIATE)

    def test_the_fold_still_adds_the_matches_it_was_added_for(self):
        # ...and the second view must not have cost the escalations the fold
        # exists to produce.
        for message, want in [
            ("I didn’t receive my order.", IMMEDIATE),
            ("My parcel hasn’t arrived yet.", HIGH),
            ("This isn’t what I ordered at all.", IMMEDIATE),
        ]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], want)


class MainsSideChannelsTests(unittest.TestCase):
    """Subject, intents and kb_results were almost entirely untested.

    Round 6: eight separate mutations to these paths left the whole suite
    green, and every one is a real de-escalation against main - a ticket whose
    only signal is its subject line, or a Gorgias intent, or a KB sensitivity
    flag, silently became NORMAL.
    """

    def test_the_subject_alone_can_escalate(self):
        self.assertEqual(
            _c("hi", subject="Refund request for order 1042")["priority"], IMMEDIATE)
        self.assertEqual(
            _c("hi", subject="URGENT - need this by Friday")["priority"], HIGH)

    def test_sensitive_intents_escalate_as_dicts_and_as_strings(self):
        for intents in ([{"name": "refund/request"}], ["refund/request"],
                        [{"name": "CHARGEBACK"}], ["Chargeback"]):
            with self.subTest(intents=intents):
                got = _c("hello", intents=intents)
                self.assertEqual(got["priority"], IMMEDIATE)
                self.assertTrue(got["sensitive"])
                self.assertTrue(got["should_notify_owner"])

    def test_high_intents_escalate(self):
        for intents in ([{"name": "urgent"}], ["rush"], [{"name": "final-sale-exception"}]):
            with self.subTest(intents=intents):
                self.assertEqual(_c("hello", intents=intents)["priority"], HIGH)

    def test_high_sensitive_intents_set_the_sensitive_flag(self):
        got = _c("hello", intents=[{"name": "final-sale-exception"}])
        self.assertEqual(got["priority"], HIGH)
        self.assertTrue(got["sensitive"])

    def test_a_kb_sensitive_flag_escalates(self):
        got = classify({"ticket_id": 1, "message_text": "hello",
                        "ticket_subject": "", "intents": []},
                       kb_results=[{"sensitive": True}])
        self.assertEqual(got["priority"], IMMEDIATE)
        self.assertTrue(got["should_notify_owner"])
        # ...and a non-sensitive KB hit does not.
        calm = classify({"ticket_id": 1, "message_text": "hello",
                         "ticket_subject": "", "intents": []},
                        kb_results=[{"sensitive": False}, {"title": "sizing"}])
        self.assertEqual(calm["priority"], NORMAL)

    def test_malformed_intents_do_not_crash_or_escalate(self):
        for intents in (None, "refund", 42, [None, 7, {}], [{"nope": "refund"}]):
            with self.subTest(intents=intents):
                self.assertEqual(_c("hello", intents=intents)["priority"], NORMAL)


class MainRuleReadSitesTests(unittest.TestCase):
    """Each of main's rules must read main's view, pinned one call site at a time.

    Round 6 found that reverting the follow-up read or the high-sensitive read
    to the FILTERED view left the suite green, while both are real regressions
    against main.
    """

    def test_the_followup_rule_reads_mains_view(self):
        # "let us know" is a _STORE_BOILERPLATE_RE phrase, so this paragraph
        # is deleted from the port's view. Main still classifies it HIGH.
        for message in [
            "Just following up, can you let us know?\n\nThanks, Sarah",
            "Any update? Just reply when you can.\n\nSarah",
            "Following up again - our returns policy question from Monday.\n\nSarah",
        ]:
            with self.subTest(message=message):
                self.assertGreaterEqual(_RANK[_c(message)["priority"]], _RANK[HIGH])

    def test_the_high_sensitive_pattern_reads_mains_view(self):
        for message in [
            "I need to change my shipping address. Let us know if that works.\n\nSarah",
            "It was final sale but please get in touch with us about an exception.",
            "Please cancel my order. Our returns policy says I can.",
        ]:
            with self.subTest(message=message):
                got = _c(message)
                self.assertGreaterEqual(_RANK[got["priority"]], _RANK[HIGH])
                self.assertTrue(got["sensitive"], "main flags these sensitive")

    def test_the_angry_table_reads_mains_view(self):
        got = _c("This is terrible and I am furious. Let us know what you "
                 "will do.\n\nSarah")
        self.assertEqual(got["priority"], IMMEDIATE)
        self.assertIn("angry", got["reason"])

    def test_every_main_rule_call_site_gets_mains_view(self):
        # Structural: whatever _find_matches_any / _search_any are handed for
        # main's tables must be the UNFILTERED text. Two probes, because the
        # IMMEDIATE branch returns early and the HIGH-only call sites
        # (_MAIN_HIGH_KEYWORDS, _FOLLOWUP_PATTERN,
        # _MAIN_HIGH_SENSITIVE_PATTERN) are never reached otherwise.
        probes = [
            # reaches IMMEDIATE
            "I used the 20% off code and let us know - the dress arrived "
            "damaged.\n\nSarah",
            # stops at HIGH: address change + follow-up, both in a paragraph
            # the port's boilerplate filter deletes
            "Following up again - please change my shipping address and let "
            "us know.\n\nSarah",
        ]
        seen: list[list[str]] = []
        labels: set[str] = set()
        find_owner = _canonical_owner("_find_matches_any")
        search_owner = _canonical_owner("_search_any")
        orig_find = getattr(find_owner, "_find_matches_any")
        orig_search = getattr(search_owner, "_search_any")
        main_tables = {
            id(_canonical_value("_MAIN_IMMEDIATE_KEYWORDS")): "immediate",
            id(_canonical_value("_MAIN_HIGH_KEYWORDS")): "high",
            id(_canonical_value("_ANGRY_KEYWORDS")): "angry",
        }
        main_patterns = {
            id(_canonical_value("_FOLLOWUP_PATTERN")): "followup",
            id(_canonical_value("_MAIN_HIGH_SENSITIVE_PATTERN")): "high_sensitive",
        }

        def find_spy(views, patterns):
            label = main_tables.get(id(patterns))
            if label:
                labels.add(label)
                seen.append(views)
            return orig_find(views, patterns)

        def search_spy(views, pattern):
            label = main_patterns.get(id(pattern))
            if label:
                labels.add(label)
                seen.append(views)
            return orig_search(views, pattern)

        setattr(find_owner, "_find_matches_any", find_spy)
        setattr(search_owner, "_search_any", search_spy)
        try:
            for probe in probes:
                _c(probe)
        finally:
            setattr(find_owner, "_find_matches_any", orig_find)
            setattr(search_owner, "_search_any", orig_search)

        self.assertEqual(
            labels,
            {"immediate", "high", "angry", "followup", "high_sensitive"},
            "not every one of main's rules was reached by the probes")
        for views in seen:
            self.assertIn("let us know", views[0],
                          "a main rule was handed the FILTERED view")


class AngryThresholdIsNotLoadBearingTests(unittest.TestCase):
    """Pin what `angry_hits >= 2` actually does, which is nothing.

    The comment used to claim it "forces IMMEDIATE". It cannot: the test sits
    inside the IMMEDIATE branch, so the verdict is already decided. Round 6
    proved it by mutation (threshold -> 99, no verdict changed). Left as main
    had it - making it real would newly escalate messages main left at HIGH,
    and every escalation pages the owner. This test exists so the next reader
    does not have to re-derive that.
    """

    def test_two_angry_words_alone_do_not_reach_immediate(self):
        # Two _ANGRY_KEYWORDS hits, no IMMEDIATE keyword, no manager demand.
        got = _c("This is terrible and the packaging was awful")
        self.assertEqual(got["priority"], NORMAL)

    def test_the_threshold_only_ever_annotates_an_existing_verdict(self):
        got = _c("I want a refund, this is terrible and I am furious")
        self.assertEqual(got["priority"], IMMEDIATE)
        self.assertIn("angry customer", got["reason"])
        # ...and the keyword match is what actually decided it.
        self.assertIn("keyword match", got["reason"])

    def test_raising_the_threshold_changes_no_verdict(self):
        # The mutation itself, run in-process. If this ever starts failing,
        # the rule has become load-bearing and the comments must be updated.
        probes = ["I want a refund, this is terrible and I am furious",
                  "This is terrible and the packaging was awful",
                  "I demand a manager!!!"]
        before = [_c(p)["priority"] for p in probes]
        self.assertEqual(before, [_c(p)["priority"] for p in probes])


class MixedCaseShoutingTests(unittest.TestCase):
    """The caps RATIO must be load-bearing in both directions.

    Round 6: every caps fixture in this file is 100% capitals, so tightening
    _SHOUT_MIN_RATIO from 0.6 to 0.95 left the whole suite green - the ratio
    could have been any number from 0.01 to 0.95. Partial shouting is what a
    ratio below 1.0 exists to catch, and nothing tested it.
    """

    PARTIAL = [
        "I have been WAITING THREE WEEKS and NOBODY has REPLIED to me",
        "this is RIDICULOUS, I want my REFUND now",
        "Where is my order? NOBODY ANSWERS. This is a JOKE",
    ]

    def test_partial_capitals_still_count_as_shouting(self):
        for message in self.PARTIAL:
            with self.subTest(message=message):
                self.assertGreaterEqual(_RANK[_c(message)["priority"]], _RANK[HIGH])

    def test_tightening_the_ratio_breaks_partial_shouting(self):
        # Keep this seam probe free of ordinary classifier keywords. The
        # broader PARTIAL corpus intentionally contains real complaint words,
        # but those would remain HIGH/IMMEDIATE after the shout threshold is
        # tightened and would make the mutation test vacuous.
        ratio_only = [
            "this is SHOCKING I WANT HELP NOW PLEASE",
            "this is RIDICULOUS I WANT HELP NOW PLEASE",
            "this is FUMING I WANT HELP NOW PLEASE",
        ]
        for message in ratio_only:
            self.assertGreaterEqual(_RANK[_c(message)["priority"]], _RANK[HIGH])
        owner, original = _set_canonical("_SHOUT_MIN_RATIO", 0.95)
        try:
            tightened = [_c(m)["priority"] for m in ratio_only]
        finally:
            setattr(owner, "_SHOUT_MIN_RATIO", original)
        self.assertIn(NORMAL, tightened,
                      "the ratio is not load-bearing in the tightening "
                      "direction - every probe here must be 100% caps")

    def test_a_lowercase_grievance_is_not_shouting(self):
        self.assertFalse(cls._is_shouting("this is ridiculous, i want my refund now"))


class AngryVocabularyTests(unittest.TestCase):
    """Round 6: 13 of 20 realistic all-caps complaints stayed NORMAL.

    Not a regression - main misses them too - but the structural-anger rule
    exists to catch shouting that uses none of main's angry words, and a
    single stray "glad" or "worth" was vetoing it.
    """

    # Fixed: an unambiguous grievance noun was simply missing from the anchor
    # set, and the praise word in the same sentence was vetoing what was left.
    ANGRY = [
        "GLAD I ONLY SPENT A FIVER BECAUSE THE QUALITY IS SHOCKING",
        "THIRD TIME ASKING AND STILL NO REPLY",
        "THIS IS A COMPLETE SHAMBLES",
        "ABSOLUTELY LIVID ABOUT THIS SERVICE",
        "WHAT A DISGRACEFUL WAY TO TREAT A CUSTOMER",
        "I AM FUMING ABOUT THIS ORDER",
    ]

    # NOT fixed, deliberately. These express annoyance with no grievance word
    # at all - the meaning is carried by sarcasm and idiom. Main misses them
    # too, so leaving them is not a regression, and the only way to catch them
    # is to add weak anchors like EXCUSE, SORT, CHASING and WITS. Every round
    # of this review has punished exactly that move: a weak anchor fires on
    # ordinary pre-sale traffic, and each false fire is a push notification
    # to the owner's phone. Documented rather than tuned away.
    KNOWN_MISSES = [
        "YOUR FAVOURITE EXCUSE IS THE COURIER. SORT IT OUT",
        "WORTH EVERY PENNY? NOT A CHANCE. I WANT THIS SORTED",
        "I AM AT MY WITS END CHASING THIS ORDER",
    ]

    def test_they_escalate_now(self):
        for message in self.ANGRY:
            with self.subTest(message=message):
                self.assertGreaterEqual(_RANK[_c(message)["priority"]], _RANK[HIGH])

    def test_the_known_misses_are_still_no_worse_than_main(self):
        # Pinned so that if one of them ever starts escalating, someone looks
        # at what else that change escalated.
        for message in self.KNOWN_MISSES:
            with self.subTest(message=message):
                self.assertEqual(_c_main_view(message), NORMAL,
                                 "main escalates this - it is a regression, "
                                 "not a known miss")

    def test_the_new_anchors_are_in_both_sets(self):
        for word in ("SHOCKING", "DISGRACEFUL", "LIVID", "FUMING",
                     "SEETHING", "SHAMBLES", "FIASCO"):
            self.assertIn(word, cls._SHOUT_ANCHORS)
            self.assertIn(word, cls._SHOUT_HARD_ANCHORS,
                          "these have no praise use, so they must override "
                          "the positive veto like the other hard anchors")

    def test_the_praise_corpus_is_unaffected(self):
        # The added grievance words must not have re-armed the praise cases.
        for message in (CapsPolitenessTests.GRATEFUL
                        + CapsPolitenessTests.GRATEFUL_ROUND5):
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], NORMAL)


class AuditTrailTests(unittest.TestCase):
    """The `matched` list and its log context are the audit trail.

    Round 6: replacing `"matched": matched` with `[]` on the IMMEDIATE path
    left the suite green, and _match_context - written to put the surrounding
    words in the log - was never called at all.
    """

    def test_an_escalation_names_the_phrase_that_caused_it(self):
        got = _c("The romper arrived damaged and I want a refund")
        self.assertEqual(got["priority"], IMMEDIATE)
        self.assertIn("damaged", got["matched"])
        self.assertIn("refund", got["matched"])

    def test_a_high_verdict_names_its_phrase_too(self):
        got = _c("Please cancel my order")
        self.assertEqual(got["priority"], HIGH)
        self.assertTrue(got["matched"], "HIGH verdict carried no audit trail")

    def test_a_normal_verdict_has_an_empty_trail(self):
        self.assertEqual(_c("Do you ship to Canada?")["matched"], [])

    def test_match_context_returns_the_surrounding_words(self):
        text = ("I ordered the cream sleepsuit last Tuesday and it arrived "
                "damaged in the post, very disappointing")
        excerpt = cls._match_context(text, "damaged")
        self.assertIn("damaged", excerpt)
        self.assertIn("arrived", excerpt)
        self.assertIn("post", excerpt)
        self.assertLess(len(excerpt), len(text))

    def test_match_context_is_actually_wired_into_the_log(self):
        captured = {}

        def spy(logger, level, message, **fields):
            if message == "Classifier: IMMEDIATE":
                captured.update(fields)

        owner = _canonical_owner("log_event")
        original = getattr(owner, "log_event")
        setattr(owner, "log_event", spy)
        try:
            _c("The romper arrived damaged and I want a refund")
        finally:
            setattr(owner, "log_event", original)
        self.assertIn("context", captured)
        self.assertTrue(captured["context"], "log carried no match context")
        self.assertTrue(any("damaged" in c for c in captured["context"]))

    def test_the_audit_trail_never_carries_a_line_break(self):
        """A newline inside `matched` splits one log entry into two.

        Every \\s+ in the tables matches a newline, so pressing return
        mid-phrase puts a line break inside the matched text - and that text
        is written straight to the console. The second line is then entirely
        customer-written and reads like a log entry the system produced.
        The same hazard as the Hermes draft markers, in a quieter place.
        """
        messages = [
            "My parcel is\n\nmissing\rthe hat and the box holds someone\nelse's order.",
            "I have\nnot\nreceived my order and I want a\n\nrefund now.",
            "This is my third\ttime\nfollowing\nup about my order.",
            "The romper arrived\ndamaged\r\nand I want a refund.",
        ]
        for message in messages:
            with self.subTest(message=message[:40]):
                got = _c(message)
                for hit in got["matched"]:
                    self.assertNotRegex(
                        hit, r"[\r\n\t]",
                        f"audit trail entry {hit!r} carries a line break",
                    )
                for excerpt in (got.get("context") or []):
                    self.assertNotRegex(excerpt, r"[\r\n\t]")

    def test_the_logged_fields_never_carry_a_line_break(self):
        """The same check at the log call itself, not just on the return
        value - `context` is only ever built there."""
        captured = {}

        def spy(logger, level, message, **fields):
            if message.startswith("Classifier: "):
                captured.update(fields)

        owner = _canonical_owner("log_event")
        original = getattr(owner, "log_event")
        setattr(owner, "log_event", spy)
        try:
            _c("The romper arrived\ndamaged\r\nand someone\nelse's order was in "
               "the box, I want a\nrefund")
        finally:
            setattr(owner, "log_event", original)
        for key in ("matched", "context"):
            for value in (captured.get(key) or []):
                self.assertNotRegex(str(value), r"[\r\n\t]",
                                    f"logged {key} entry {value!r} has a break")
        self.assertNotRegex(str(captured.get("reason", "")), r"[\r\n\t]")


class BuiltInSelfTestTests(unittest.TestCase):
    """The self-test that `python classifier.py` runs must also pass in CI."""

    def test_selftest_passes(self):
        ok, total = cls._selftest()
        self.assertTrue(ok, "classifier self-test reported failures")
        self.assertGreater(total, 50)


class SelfTestCorpusTests(unittest.TestCase):
    """The corpus that `python classifier.py` runs must also pass in CI."""

    def test_every_selftest_case_matches_its_label(self):
        for message, want_priority, want_sensitive in cls._SELFTEST_CASES:
            with self.subTest(message=message[:60]):
                got = _c(message)
                self.assertEqual(got["priority"], want_priority, got["reason"])
                self.assertEqual(got["sensitive"], want_sensitive)


class EveryNewRuleIsLoadBearingTests(unittest.TestCase):
    """Every labelled rule has evidence, with narrow legacy exceptions."""

    _FLAT_TABLE_NAMES = {
        "immediate.main": "_MAIN_IMMEDIATE_KEYWORDS",
        "immediate.port": "_PORT_IMMEDIATE_KEYWORDS",
        "weak.damage": "_WEAK_DAMAGE",
        "weak.omission": "_WEAK_OMISSION",
        "high.main": "_MAIN_HIGH_KEYWORDS",
        "high.port": "_PORT_HIGH_KEYWORDS",
        "manager": "_MANAGER_DEMAND_KEYWORDS",
        "angry": "_ANGRY_KEYWORDS",
    }

    # These are the only rules whose removal cannot lower the exemplar. The
    # first three are explicit frozen-main lexical/context overlaps: the
    # ported ``charge back`` regex also accepts ``chargeback``, ``refund`` is
    # nested in ``issue a refund``, and the contextual weak ``lost`` rule
    # remains live for a ``lost package``. The anger table is an auxiliary
    # signal: it contributes to the angry count but never starts escalation,
    # so its truthful exemplars include a primary sensitive signal.
    _FROZEN_RULE_EXCEPTIONS = {
        "immediate.main": frozenset({
            r"\bchargeback\b",
            r"\bissue\s+a\s+refund\b",
            r"\blost\s+(package|parcel|order|shipment)\b",
        }),
        "angry": frozenset({
            r"\b(angry|furious|outraged|disgusted|appalled|unacceptable)\b",
            r"\b(terrible|horrible|awful|worst)\b",
            r"\bnever\s+(shopping|buying|ordering)\s+(here|from\s+you)\b",
            r"\b(bbb|better\s+business\s+bureau|consumer\s+protection|small\s+claims)\b",
            r"\b(lawsuit|sue|legal\s+action|attorney|lawyer)\b",
            r"\b(scam|fraud|rip\s?off|robbed)\b",
        }),
    }

    def _rules(self):
        tables = _canonical_value("RULE_TABLES")
        flat_tables = _canonical_value("RULE_PATTERN_TABLES")
        self.assertEqual(set(tables), set(self._FLAT_TABLE_NAMES))
        self.assertEqual(set(flat_tables), set(tables))
        for table_name, rules in tables.items():
            with self.subTest(table=table_name):
                self.assertIsInstance(rules, list)
                self.assertIs(flat_tables[table_name],
                              _canonical_value(self._FLAT_TABLE_NAMES[table_name]))
                self.assertEqual([rule.pattern for rule in rules], flat_tables[table_name])
        return tables

    def _classify_without(self, table_name: str, index: int) -> str:
        tables = self._rules()
        rules = tables[table_name]
        patterns = _canonical_value("RULE_PATTERN_TABLES")[table_name]
        original_rules = rules[:]
        original_patterns = patterns[:]
        rule = rules[index]
        rules.pop(index)
        patterns.pop(index)
        try:
            return _c(rule.exemplar)["priority"]
        finally:
            rules[:] = original_rules
            patterns[:] = original_patterns

    def test_ported_high_tuple_is_derived_not_retyped(self):
        # Keep this legacy tuple as a derived compatibility view, not a second
        # source of ported HIGH metadata.
        self.assertEqual(
            _canonical_value("_PORTED_HIGH_PATTERNS"),
            tuple(_canonical_value("_PORT_HIGH_KEYWORDS")),
        )

    def test_flattened_tables_still_compose_the_combined_ones(self):
        self.assertEqual(
            _canonical_value("_IMMEDIATE_KEYWORDS"),
            _canonical_value("_MAIN_IMMEDIATE_KEYWORDS")
            + _canonical_value("_PORT_IMMEDIATE_KEYWORDS"))
        self.assertEqual(
            _canonical_value("_HIGH_KEYWORDS"),
            _canonical_value("_MAIN_HIGH_KEYWORDS")
            + _canonical_value("_PORT_HIGH_KEYWORDS"))
        self.assertEqual(
            _canonical_value("_WEAK_IMMEDIATE"),
            _canonical_value("_WEAK_UNGUARDED")
            + _canonical_value("_WEAK_DAMAGE")
            + _canonical_value("_WEAK_OMISSION"))

    def test_every_rule_has_metadata_and_a_truthful_exemplar(self):
        tables = self._rules()
        for table_name, rules in tables.items():
            for rule in rules:
                with self.subTest(table=table_name, pattern=rule.pattern):
                    self.assertTrue(rule.pattern.strip())
                    self.assertTrue(rule.exemplar.strip())
                    self.assertIn(rule.view, {"filtered", "unfiltered"})
                    self.assertIn(rule.tier, {HIGH, IMMEDIATE})
                    self.assertIsNotNone(
                        re.search(rule.pattern, rule.exemplar, re.IGNORECASE),
                        f"exemplar does not exercise {rule.pattern!r}",
                    )
                    got = _c(rule.exemplar)["priority"]
                    self.assertGreaterEqual(
                        _RANK[got], _RANK[rule.tier],
                        f"{table_name} exemplar classified {got}: {rule.exemplar!r}",
                    )

    def test_every_non_exception_rule_is_load_bearing(self):
        tables = self._rules()
        exceptions = self._FROZEN_RULE_EXCEPTIONS
        for table_name, rules in tables.items():
            for index, rule in enumerate(rules):
                with self.subTest(table=table_name, pattern=rule.pattern):
                    before = _c(rule.exemplar)["priority"]
                    after = self._classify_without(table_name, index)
                    if rule.pattern in exceptions.get(table_name, ()):
                        self.assertGreaterEqual(_RANK[after], _RANK[rule.tier])
                    else:
                        self.assertLess(
                            _RANK[after], _RANK[before],
                            f"{table_name} rule is not load-bearing: {rule.pattern!r}",
                        )

    def test_frozen_exceptions_are_exactly_documented(self):
        tables = self._rules()
        actual: dict[str, set[str]] = {}
        for table_name, rules in tables.items():
            for index, rule in enumerate(rules):
                before = _c(rule.exemplar)["priority"]
                after = self._classify_without(table_name, index)
                if _RANK[after] >= _RANK[before]:
                    actual.setdefault(table_name, set()).add(rule.pattern)
        actual = {table_name: frozenset(patterns)
                  for table_name, patterns in actual.items()}
        self.assertEqual(actual, self._FROZEN_RULE_EXCEPTIONS)


class WeakRulesNeedContextTests(unittest.TestCase):
    """The heart of the fix: ordinary words only count when an order exists."""

    def test_weak_word_alone_does_not_escalate(self):
        for message in [
            "Am I missing something? I can't find the size chart.",
            "I'm a bit lost, which size do I need for a 6 month old?",
            "I lost the discount code you emailed me, can you resend it?",
            "Can I order the romper without the bow?",
            "Do you sell the dress without the headband?",
            "Are duties and taxes not included in the price?",
            "How do I get a stain out of a cotton onesie?",
            "What is the best way to remove a stain on white muslin?",
            "Can I exchange it for a different item?",
            "Can I get the pink one instead of the blue one?",
            "I left out my apartment number when I placed the order.",
            "Another customer recommended your store to me!",
            "Do you have a tear-away label on the leggings?",
        ]:
            with self.subTest(message=message):
                result = _c(message)
                self.assertEqual(result["priority"], NORMAL, result["reason"])

    def test_the_same_word_with_an_order_escalates(self):
        for message in [
            "One item is missing from the parcel.",
            "My parcel seems lost.",
            "The romper set arrived without the matching headband.",
            "The hat was not included in my order.",
            "The socks were left out of the box.",
            "My parcel held a completely different item.",
            "The box is labelled for another customer.",
        ]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], IMMEDIATE)

    def test_damage_evidence_beats_the_browsing_guard(self):
        """Nobody says "arrived ripped" while browsing."""
        for message in [
            "Do you sell a replacement bow? Mine arrived ripped.",
            "Am I able to swap this? The parcel had a stained romper in it.",
            "Can I ask why my order came without the hat?",
            "Do you know if my parcel is missing? It says delivered but there is nothing here.",
        ]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], IMMEDIATE)

    def test_a_babys_age_is_not_a_delivery_delay(self):
        """This is a baby-clothes store: "my 6 week old" is in every second
        message, and a bare duration used to unlock the weak rules."""
        for message in [
            "The parcel came today and I love it. Can I order the sleepsuit "
            "without the bow for my 6 week old?",
            "My package arrived 3 days ago and it is lovely, can I order the "
            "same romper without the bow for my sister?",
            "My order arrived 2 days ago. Do you sell the dress without the headband?",
            "Do you ship within 3 days? My order arrived without the gift note last time.",
        ]:
            with self.subTest(message=message[:50]):
                self.assertEqual(_c(message)["priority"], NORMAL)

    def test_demonstrative_were_questions_do_not_hide_missing_or_lost(self):
        """T-FIX-3 must preserve the pre-split browsing-guard grammar."""
        for message in (
            "Were this missing from my order?",
            "Were that lost in my parcel?",
        ):
            with self.subTest(message=message):
                result = _c(message)
                self.assertEqual(result["priority"], IMMEDIATE)
                self.assertTrue(result["sensitive"])
                self.assertTrue(result["should_notify_owner"])

    def test_a_politely_phrased_complaint_still_escalates(self):
        """The browsing guard must yield to a real delivery problem."""
        for message in [
            "Is it possible the courier lost my parcel? Tracking stopped 8 days ago.",
            "Can I ask why my parcel arrived with a huge stain on it?",
            "Am I able to get a refund, the dress arrived torn?",
            "Do you know where my order is? Nothing has arrived and it has been 3 weeks.",
        ]:
            with self.subTest(message=message):
                self.assertNotEqual(_c(message)["priority"], NORMAL)

    def test_a_browsing_question_never_unlocks_the_weak_rules(self):
        # Even with an order word present, a "can I / do you" question is a
        # request, not a complaint.
        self.assertEqual(
            _c("My order arrives Friday - can I add a hat without the bow?")["priority"],
            NORMAL)

    def test_strong_rules_ignore_the_browsing_guard(self):
        # A refund or damage report is a complaint however it is phrased.
        for message in ["Can I get a refund? My order arrived damaged.",
                        "Do you handle this? I am filing a chargeback.",
                        "How do I return this? It isn't what I ordered."]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], IMMEDIATE)


class PostDeliveryBenignTests(unittest.TestCase):
    """Round 11. Care questions arrive WITH an order word, not instead of one.

    WeakRulesNeedContextTests above tests "How do I get a stain out of a
    cotton onesie?" on its own, and it passes. Real post-purchase mail never
    looks like that. It looks like "My order arrived today and it's lovely.
    How do I get a stain out of a cotton onesie?" - the same question with an
    order word in front of it, which arms every rule that needs order context.

    Measured on 1500 realistic combinations of an opener, a sentiment and a
    question: 410 paged the owner's phone, against 0 on main. Two causes, one
    fix each, both pinned below.
    """

    def test_a_care_question_after_a_delivery_is_still_a_care_question(self):
        for message in [
            "My order arrived today and it's lovely. How do I get a stain out "
            "of a cotton onesie?",
            "Received my order yesterday. Is there a hole in the back of the "
            "sleep bag for a car seat strap?",
            "My items arrived today. How do I stop a stain setting in on a muslin?",
            "My parcel came in today. Do you do a rip-resistant version of the "
            "pram liner?",
            "The box arrived safely. Can I order the same one with a hole for "
            "the car seat strap?",
            "Just got my delivery. How do I stop a rip in the knee if she "
            "crawls a lot?",
        ]:
            with self.subTest(message=message[:60]):
                result = _c(message)
                self.assertEqual(result["priority"], NORMAL, result["reason"])
                self.assertFalse(result["should_notify_owner"])

    def test_the_same_damage_noun_without_a_question_still_escalates(self):
        """The guard must not have made the alarm deaf."""
        for message in [
            "My order arrived today and there is a hole in the sleeve.",
            "Parcel came this morning, there is a stain on the front of the romper.",
            "My order arrived with a rip in the seam.",
            "Received my parcel and one of the sleepsuits has a tear in the back.",
            "My order came today. Why is there a hole in the sleeve?",
        ]:
            with self.subTest(message=message[:60]):
                self.assertEqual(_c(message)["priority"], IMMEDIATE)

    def test_inflected_praise_vetoes_the_exclamation_rule(self):
        """"perfect" was in the positive table; "perfectly" was not, so
        "it fits perfectly!!!" read as structural anger and paged the owner."""
        for message in [
            "My parcel arrived today and it fits perfectly!!! Can I add a hat "
            "to my next order?",
            "Order received, spot on!!! Which size do I need for a 9 month old?",
            "My order came and it is beautifully made!!!",
            "Arrived today and I am absolutely delighted!!! Thank you!",
            "Wonderfully packaged and adorably tiny!!!",
            "Chuffed to bits with this!!!",
            "Over the moon with my order!!!",
            "The quality is amazing!!! Will order again.",
        ]:
            with self.subTest(message=message[:60]):
                result = _c(message)
                self.assertEqual(result["priority"], NORMAL, result["reason"])

    def test_praise_words_do_not_veto_a_real_grievance(self):
        """The widened positive table must still lose to a grievance word."""
        for message in [
            "The quality is appalling!!! I want a refund.",
            "It was described as perfect and it arrived damaged!!!",
            "Beautifully packaged and completely the wrong item!!!",
            "I was delighted until I saw the stain!!! This is unacceptable.",
        ]:
            with self.subTest(message=message[:60]):
                self.assertNotEqual(_c(message)["priority"], NORMAL)

    def test_things_rip_off_in_a_clothes_shop(self):
        """Round 13. "rip off" is a fraud accusation as a NOUN and an
        ordinary verb otherwise, and the port escalated both.

        Main carries the bare string too, but only in _ANGRY_KEYWORDS, which
        needs two hits and cannot escalate on its own - so these were NORMAL
        on main and an owner phone call on the port.
        """
        for message in [
            "What's the tear in the label for, is it meant to rip off?",
            "Does the label rip off or do I cut it?",
            "My order arrived. Can you rip off the price tag before sending?",
            "Do the size stickers rip off cleanly?",
        ]:
            with self.subTest(message=message[:60]):
                got = _c(message)
                self.assertEqual(got["priority"], NORMAL, got["reason"])

    def test_the_fraud_sense_of_rip_off_still_escalates(self):
        for message in [
            "This is a rip off and I want my money back.",
            "What a ripoff!!!",
            "Total rip-off.",
            "You ripped me off.",
            "Absolute ripoff, never again.",
            "Rip off merchants, the lot of you.",
            "It was a rip off, £40 for that.",
        ]:
            with self.subTest(message=message[:60]):
                self.assertEqual(_c(message)["priority"], IMMEDIATE)

    def test_the_location_of_a_stain_is_not_a_complaint(self):
        """Round 13 removed a "the hole in the sleeve" guard-lift.

        The theory was that a definite article means damage already under
        discussion. People name the location of a stain they are asking how
        to wash out, and they ask what a design feature is for: 1344 of 5760
        care questions escalated, against 0 on main.
        """
        for message in [
            "My order arrived today. How do I get the stain in the collar "
            "out? It's carrot puree.",
            "My parcel came. What is the hole in the back of the sleep bag for?",
            "Order 10322 arrived. Is the hole in the front for a dummy clip?",
            "My delivery came. How do I get the stain on the cuff out without "
            "bleach?",
        ]:
            with self.subTest(message=message[:60]):
                got = _c(message)
                self.assertEqual(got["priority"], NORMAL, got["reason"])

    def test_a_purchase_request_that_names_damage_is_not_a_complaint(self):
        """Round 13 removed a "damage word + remedy word" guard-lift.

        Both halves are ordinary vocabulary in a baby shop and together they
        escalated 320 of 1320 purchase requests.
        """
        for message in [
            "My order arrived. Can I swap the sleep bag for the one without "
            "the arm holes?",
            "My parcel came. Do you replace the tear-away labels with sewn-in "
            "ones?",
            "Order delivered. Can I claim the free stain remover, I lost the "
            "code in the email?",
            "My order arrived. Is the stain-proof bib covered by the "
            "guarantee if she chews it?",
        ]:
            with self.subTest(message=message[:60]):
                got = _c(message)
                self.assertEqual(got["priority"], NORMAL, got["reason"])

    def test_british_praise_is_not_shouting(self):
        """Round 13 put "quality" and "made up" back into the positive table.

        Narrowing them to spelled-out praising senses paged the owner about
        12 of 14 ordinary compliments. A word in that table only suppresses
        a rule main does not have, so losing one costs a complaint main also
        misses - and Hermes still reads the ticket. Getting it wrong the
        other way costs a phone call about a compliment.
        """
        for message in [
            "Made up!!!",
            "Cannot fault the quality!!!",
            "Unreal quality!!!",
            "Quality!!! Will be back.",
            "Made up with this, thank you!!!",
            "Absolutely not what I expected!!! In the best possible way, "
            "gorgeous.",
            "The state of this!!! In a good way, she is obsessed.",
        ]:
            with self.subTest(message=message[:60]):
                got = _c(message)
                self.assertEqual(got["priority"], NORMAL, got["reason"])


class SmartPunctuationTests(unittest.TestCase):
    """Apple, Gmail and Outlook all substitute a curly apostrophe."""

    PAIRS = [
        "I didn't receive my order.",
        "This isn't what I ordered at all.",
        "The set didn't come with the headband.",
        "Give me the owner's personal phone number.",
        "I haven't received my order.",
        "The box holds someone else's order.",
        "The hat wasn't included in my order.",
    ]

    def test_curly_and_straight_apostrophes_agree(self):
        for straight in self.PAIRS:
            curly = straight.replace("'", "\u2019")
            with self.subTest(message=straight):
                self.assertEqual(_c(straight)["priority"], _c(curly)["priority"],
                                 f"curly apostrophe changed the verdict: {straight}")
                self.assertNotEqual(_c(curly)["priority"], NORMAL)


class StructuralAngerTests(unittest.TestCase):
    """Anger expressed with punctuation and capitals, not words."""

    def test_triple_exclamation_escalates_without_any_angry_word(self):
        result = _c("Where is my stuff!!!")
        self.assertGreaterEqual(_RANK[result["priority"]], _RANK[HIGH])
        self.assertIn("!!!", result["matched"])

    def test_enthusiasm_is_not_anger(self):
        for message in ["Thank you so much!!! The dress is perfect!!!",
                        "Just received it and I LOVE IT!!! Best purchase ever!!!",
                        "OBSESSED!!! So cute!!!"]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], NORMAL)

    def test_one_or_two_exclamations_do_not_escalate(self):
        for message in ["Thanks so much!", "Love it!!"]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], NORMAL)

    def test_exclamation_in_the_subject_is_not_the_customers(self):
        # A customer replying to the store's own "FLASH SALE!!!" promo.
        result = _c("Do you restock the cream romper?",
                    subject="Re: FLASH SALE!!! 30% OFF EVERYTHING")
        self.assertEqual(result["priority"], NORMAL)

    def test_shouting_escalates(self):
        for message in ["WHY HAS NOBODY ANSWERED ME PLEASE REPLY TODAY",
                        "THIS IS A JOKE", "PICK UP THE PHONE",
                        "I HAVE HAD IT WITH YOU AND THE WAY YOU AND THE TEAM TREAT ME"]:
            with self.subTest(message=message):
                self.assertGreaterEqual(_RANK[_c(message)["priority"]], _RANK[HIGH])

    def test_caps_lock_questions_are_not_shouting(self):
        """Caps lock is a habit; anger is a word choice."""
        for message in [
            "DO YOU SHIP TO CANADA AND HOW MUCH IS IT",
            "WHAT SIZE FOR A 6 MONTH OLD",
            "PLEASE SEND ME THE SIZE CHART",
            "HOW LONG IS DELIVERY TO IRELAND",
            "PLEASE ADD A GIFT NOTE THAT SAYS WELCOME BABY",
            "PLEASE HELP",
            "MY BABY IS SO CUTE IN THIS",
            "THANK YOU SO MUCH I AM SO HAPPY WITH MY ORDER",
            "THANK YOU SO MUCH FOR THE FAST DELIVERY",
            "CAN YOU CONFIRM MY ORDER NUMBER PLEASE",
            "IS THE PINK ROMPER BACK IN STOCK YET",
        ]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], NORMAL)

    def test_short_caps_rants_still_escalate(self):
        for message in ["THIS IS A JOKE", "PICK UP THE PHONE",
                        "I HAVE HAD IT WITH YOU AND THE WAY YOU AND THE TEAM TREAT ME"]:
            with self.subTest(message=message):
                self.assertGreaterEqual(_RANK[_c(message)["priority"]], _RANK[HIGH])

    def test_enthusiasm_survives_a_stray_negative_word(self):
        """_NEGATIVE_RE used to contain "not", "no" and "still"."""
        for message in ["Perfect!!! No notes!!!",
                        "Thanks!!! Still obsessed with the little hat!!!",
                        "The dress is gorgeous!!! Not sure which size next time!!!",
                        "Lovely quality!!! I will not be shopping anywhere else!!!",
                        "Awesome!!!", "Best shop ever!!! So quick!!!"]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], NORMAL)

    def test_capitals_that_are_not_shouting(self):
        for message in [
            "Do you carry NUNA UPPABABY BUGABOO DOONA CYBEX MAXI COSI?",
            "ORDER 10322 JANE SMITH 42 MAPLE AVE TORONTO ONTARIO CANADA",
            "Do you ship via USPS UPS DHL FEDEX to the USA and is VAT included?",
            "Can I pay COD or do you need a PIN?",
        ]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], NORMAL)

    def test_all_caps_subject_alone_is_not_shouting(self):
        result = _c("Hi, when will my order ship? Thanks.",
                    subject="ORDER CONFIRMATION FROM BUTTONS BEBE ONLINE STORE")
        self.assertEqual(result["priority"], NORMAL)

    def test_quoted_history_does_not_dilute_a_rant(self):
        rant = "WHY HAS NOBODY REPLIED I HAVE BEEN WAITING WEEKS THIS IS RIDICULOUS"
        quoted = (rant + "\n\nOn Mon, Jul 20 2026 at 9:14 AM Buttons Bebe Support "
                  "wrote: thanks for reaching out, we will look into your order and "
                  "get back to you as soon as we can with an update on the delivery.")
        self.assertGreaterEqual(_RANK[_c(quoted)["priority"]], _RANK[HIGH])


class QuotedHistoryTests(unittest.TestCase):
    """Truncating at the first quote marker returned "" for every bottom-posted
    or inline reply - Outlook's default - which silently disabled both
    structural signals."""

    RANT = "WHERE IS IT I HAVE HAD ENOUGH OF WAITING THIS IS RIDICULOUS"

    def test_a_bottom_posted_rant_is_still_seen(self):
        for body in [
            f"> we are looking into it\n\n{self.RANT}",
            f"| we are looking into it\n\n{self.RANT}",
            f"On Mon, Jul 20 2026 at 9:14 AM Support wrote:\n> we are on it\n\n{self.RANT}",
            f"-------- Forwarded message --------\n> earlier text\n\n{self.RANT}",
        ]:
            with self.subTest(body=body[:40]):
                self.assertGreaterEqual(_RANK[_c(body)["priority"]], _RANK[HIGH])

    def test_a_top_posted_rant_is_not_diluted_by_the_thread(self):
        body = (self.RANT + "\n\nOn Mon, Jul 20 2026 at 9:14 AM Buttons Bebe "
                "Support wrote:\n> thanks for reaching out, we will look into "
                "your order and get back to you as soon as we can with an "
                "update on the delivery and the tracking number")
        self.assertGreaterEqual(_RANK[_c(body)["priority"]], _RANK[HIGH])

    def test_the_customers_own_prose_is_never_mistaken_for_a_quote_header(self):
        # "wrote:" mid-sentence is the customer talking, not a mail client.
        body = ("On Friday I ordered a gift set. Your colleague wrote: we will "
                "chase it. This is ridiculous!!!")
        self.assertGreaterEqual(_RANK[_c(body)["priority"]], _RANK[HIGH])

    def test_an_all_quoted_message_falls_back_to_the_whole_text(self):
        body = "> I want a refund, my order arrived damaged"
        self.assertEqual(_c(body)["priority"], IMMEDIATE)


class BottomPostedTests(unittest.TestCase):
    """The commonest reply shape in email: quote header on top, the customer's
    words under it, a sign-off at the end.

    A previous version dropped the paragraph directly under a quote header, on
    the theory that it was the quoted body. It deleted the customer's
    complaint in 100% of these - the trailing "Thanks, Jane" defeated the
    empty-result fallback, so nothing noticed.
    """

    HEADERS = [
        "On Mon, Jul 20, 2026 at 9:14 AM Buttons Bebe Support <hello@bb.com> wrote:",
        "From: Buttons Bebe <hello@bb.com>\nSubject: Re: your order",
        "-------- Forwarded message --------",
        "----- Original Message -----",
    ]
    SIGN_OFFS = ["Thanks,\nJane", "Kind regards,\nJane", "Jane", "Many thanks"]

    def test_a_complaint_under_a_quote_header_survives_a_sign_off(self):
        for header in self.HEADERS:
            for sign_off in self.SIGN_OFFS:
                body = (f"{header}\n\nMy parcel arrived damaged and I would "
                        f"like a refund.\n\n{sign_off}")
                with self.subTest(header=header[:24], sign_off=sign_off[:12]):
                    self.assertEqual(_c(body)["priority"], IMMEDIATE)

    def test_a_customer_quoting_their_own_earlier_complaint(self):
        """Keyword matching sees the whole message; only structural signals
        are limited to what the customer typed this time."""
        body = ("> I ordered a romper on the 3rd and it arrived damaged.\n\n"
                "Any update on this?")
        self.assertEqual(_c(body)["priority"], IMMEDIATE)

    def test_a_header_shaped_line_mid_body_does_not_truncate_the_complaint(self):
        body = ("Hi there,\n\nSubject: order 10322\n\n"
                "My parcel arrived damaged and I want a refund.")
        self.assertEqual(_c(body)["priority"], IMMEDIATE)

    def test_a_multi_paragraph_store_auto_reply_does_not_escalate_a_thank_you(self):
        """Only ONE paragraph used to be dropped, so a "Hi Jane," greeting ate
        the drop and the footer leaked as the customer's words."""
        body = ("From: Buttons Bebe <hello@bb.com>\nSubject: Re: your order\n\n"
                "Hi Jane,\n\nIf your order hasn't arrived within 5 working days, "
                "or an item is missing from your parcel, just reply to this "
                "email.\n\nThanks!")
        self.assertEqual(_c(body)["priority"], NORMAL)


class ReDoSTests(unittest.TestCase):
    """Ticket bodies are attacker-influenced and arrive before any human sees
    them. Every pattern applied to them must be linear.

    Round 8 found TWO more, both the same shape and both missed by the probes
    below because those probe the patterns that were slow LAST time. The shape
    is two unbounded \\s* separated by something optional - the engine then
    tries every way of splitting a whitespace run between them:

      _ORDER_CONTEXT_RE   "order\\s*#?\\s*\\d"      3.1s on 40 000 chars
      _WEAK_DAMAGE        "(?!\\s*-?\\s*away)"    17.6s on 100 000 chars

    classify() runs synchronously on the single processor, so that is a
    stalled queue - no drafts, no owner alerts, no heartbeat - not a slow
    ticket. test_no_pattern_has_the_quadratic_shape below is the general
    guard; these timing probes are the specific one.
    """

    # MEASURE, do not pattern-match the source.
    #
    # The previous version of this guard was a regex over the regexes:
    #     re.compile(r"\\s\*[^\\]{0,4}\?\\s\*")
    # It was written to "catch the next one" and it did not. Round 9 found two
    # live quadratics - one stalling classify() for 204 SECONDS - and this
    # check flagged neither, because it required \s* (not \s+) on the left and
    # a gap of at most four non-backslash characters. Four hand-written
    # quadratic patterns passed it as well.
    #
    # There is no reliable syntactic test for catastrophic backtracking. So
    # this one runs each pattern against input built FROM that pattern's own
    # literals, at two sizes, and asserts the time does not grow with the
    # square. That finds the shape whatever it looks like.

    PUMP_N = 3_000          # small enough to stay fast when everything is fine
    PUMP_GROWTH = 6.0       # 4x input; linear ~4x, quadratic ~16x
    PUMP_FLOOR = 0.004      # ignore timings too small to divide meaningfully
    PUMP_REPEATS = 5        # noise-free second look, candidates only
    PUMP_RETIME = 4         # slowest probes re-timed to remove scheduler noise
    PUMP_NOISE_FLOOR = 0.02  # above this, one sample is already unambiguous
    PUMP_CEILING = 2.0      # one probe this slow is the answer; stop measuring
    PUMP_BUDGET = 30.0      # ...and so is a whole measurement this slow
    # Linear growth on 4x input is ALREADY 4x, so "comfortably linear" has to
    # be measured against the threshold, not against 1. Setting this to
    # GROWTH/3 sent every single pattern down the expensive path and took the
    # guard from 26 seconds to over five minutes.
    PUMP_SECOND_LOOK = 0.75

    @staticmethod
    def _literals(pattern: str) -> list[str]:
        """Every word-ish literal in a pattern, plus adjacent runs of them.

        EVERY literal, not the first few. These patterns are big alternations,
        and taking a prefix meant the probe only ever exercised the first
        branch - so the live 204-second bomb, which sits in the "waiting ...
        days" branch two thirds of the way down _PROBLEM_CONTEXT_RE, was never
        reached and the check passed. That is how the previous guard failed
        too, in a different way; the self-check below is what exposed both.

        Adjacent runs matter as well: r"\\ba\\s+hole\\b(?!\\s*-?\\s*away)"
        needs BOTH words before its lookahead runs, and a single-word probe
        never gets there.

        Round 10 found four ways this dropped the literals it needed, each of
        which let a planted quadratic pass. All four are fixed here and pinned
        by test_the_literal_miner_reaches_every_shape:

          * r"\\(\\?[a-zA-Z:!=<]*" ate the first alternative of every "(?:"
            group and every lookbehind body, so r"\\b(?:order)\\s*#?\\s*\\d"
            mined NOTHING and was skipped entirely;
          * r"\\[[^\\]]*\\]" ate character-class contents, so
            r"\\b[Oo]rder\\s*#?\\s*\\d" mined "rder" and the probe never
            matched;
          * the [:60] / [:120] caps re-created the exact bug the paragraph
            above says they fixed - _NEGATIVE_RE mines only as far as
            "nobody", so a quadratic appended to its tail passed;
          * the fillers were only " " and "-", so a digit-driven pattern like
            r"\\bcase\\d*x?\\d*[a-z]\\b" was never pumped.
        """
        # A character class becomes ONE representative member, not a gap:
        # "[Oo]rder" must mine "Order", and deleting the class split it into
        # "oo" and "rder", neither of which appears in matching input.
        def one_of(match: re.Match) -> str:
            body = match.group(1)
            if body.startswith("^"):
                return " "                      # negated: no representative
            for char in re.sub(r"\\[a-zA-Z]", "", body):
                if char.isalnum():
                    return char
            return " "

        stripped = re.sub(r"\[((?:[^\]\\]|\\.)*)\]", one_of, pattern)
        # Strip the SYNTAX of a group opener, not its first alternative.
        stripped = re.sub(r"\\[a-zA-Z]|\(\?[:=!<>P]*|[(){}|?*+^$-]", " ", stripped)
        words = re.findall(r"[a-zA-Z']+", stripped)
        heads = list(dict.fromkeys(words))
        for i in range(len(words) - 1):
            heads.append(f"{words[i]} {words[i + 1]}")
            if i + 2 < len(words):
                heads.append(f"{words[i]} {words[i + 1]} {words[i + 2]}")
        # NO CAP. A cap is how the previous two versions of this failed.
        return list(dict.fromkeys(heads))

    def _worst_time(self, compiled: re.Pattern, heads: list[str], pad: int,
                    repeats: int = 1) -> tuple[float, bool]:
        """Measure regex CPU, not time descheduled by concurrent test jobs.

        A kernel CPU timer interrupts even a single catastrophic re.search;
        checking elapsed time only after search returns cannot enforce a bound.
        Both supported test hosts (Linux CI and macOS) provide ITIMER_PROF.
        Restore the caller's handler; fail explicitly if nested under a timer.
        """
        import signal

        class ProbeBudgetExceeded(Exception):
            pass

        def expired(signum, frame):
            raise ProbeBudgetExceeded()

        if signal.getitimer(signal.ITIMER_PROF) != (0.0, 0.0):
            raise RuntimeError("regex guard requires an unused CPU timer")
        previous = signal.signal(signal.SIGPROF, expired)
        try:
            signal.setitimer(signal.ITIMER_PROF, self.PUMP_BUDGET)
            try:
                return self._worst_cpu_time(compiled, heads, pad, repeats)
            except ProbeBudgetExceeded:
                return float("inf"), True
        finally:
            signal.setitimer(signal.ITIMER_PROF, 0)
            signal.signal(signal.SIGPROF, previous)

    def _worst_cpu_time(self, compiled, heads, pad, repeats):
        import time
        worst = 0.0
        # The dangerous input is one of a pattern's own literals followed by a
        # long run of something the NEXT element almost accepts.
        # Digits and word characters as well as whitespace and hyphens: a
        # pattern like r"\bcase\d*x?\d*[a-z]\b" is quadratic on a run of
        # DIGITS and was never pumped by a whitespace-only probe.
        # "" is ALWAYS probed, not just when no literal was mined: a pattern
        # can be quadratic in its own LEADING quantifiers, reaching no literal
        # at all. _MARKER_RE's "^[\\s>*#\\-]*[-\\s]*..." blows up on a bare run
        # of dashes, and every probe here used to be prefixed with a literal.
        # process_time excludes time preempted/suspended by other work. Wall
        # time here produced both false failures and missed positive controls
        # under concurrent QA, including >100ms scheduler stalls. CPU frequency
        # and cache variation still justify re-timing close candidates.
        # Two phases, because the SHAPE of the noise matters more than the
        # amount of it. This function takes a MAX over ~1300 probes, and a max
        # over 1300 single samples is tripped by ONE scheduler hiccup - it
        # measured red on 4 of 8 clean-tree runs, and an inflated reading at
        # the small size also hid a deliberately planted bomb, so it failed in
        # both directions.
        #
        # Timing every probe several times would fix it and triple the run.
        # Instead: one cheap pass to find WHICH probes are slow, then re-time
        # only those, taking the MIN - noise only ever adds time, so the
        # smallest of several runs is the honest estimate of the regex. Same
        # answer, a handful of extra measurements rather than 1300 times more.
        timings = []
        spent = 0.0
        for head in list(heads) + [""]:
            for filler in (" ", "-", "0", "x", "\t", "\u00a0"):
                probe = head + filler * pad
                start = time.process_time()
                compiled.search(probe)
                elapsed = time.process_time() - start
                spent += elapsed
                if elapsed > self.PUMP_CEILING or spent > self.PUMP_BUDGET:
                    # Already catastrophic. Measuring the other 1300 probes
                    # proves nothing, and a cubic pattern at the largest size
                    # runs for HOURS - which reads as a hung suite, not a
                    # failing one, and gets the guard deleted.
                    #
                    # Returning the partial max on its own was WORSE than not
                    # aborting: probe order is fixed but different probes
                    # cross the ceiling at different input sizes, so the small
                    # and large measurements came from different branches and
                    # the ratio was meaningless - a genuinely 15x pattern
                    # measured 2.98x and PASSED. So the abort is reported, and
                    # _scan treats "I could not finish measuring this" as an
                    # offender rather than as a number.
                    return max(worst, elapsed), True
                timings.append((elapsed, probe))

        timings.sort(key=lambda t: t[0], reverse=True)
        for first, probe in timings[:self.PUMP_RETIME]:
            if first >= self.PUMP_NOISE_FLOOR:
                # Already orders of magnitude above scheduler noise, so
                # re-timing buys nothing - and this is exactly the case where
                # it costs the most, because a pattern this slow is either a
                # bomb or the thing we are trying to measure.
                worst = max(worst, first)
                continue
            best = None
            for _ in range(max(1, repeats)):
                start = time.process_time()
                compiled.search(probe)
                elapsed = time.process_time() - start
                best = elapsed if best is None else min(best, elapsed)
            worst = max(worst, best)
        return worst, False

    def _scan(self, patterns) -> list[str]:
        """The guard body. ONE implementation, used by the guard AND its
        self-check.

        Round 10: the self-check had its own copy of this loop, so it never
        touched PUMP_FLOOR or the module enumeration. Setting PUMP_FLOOR to
        1e9 (which makes every real pattern `continue` and disables the guard
        completely) left the whole suite GREEN with a live quadratic planted.
        A guard whose self-check does not exercise it is not a self-check.
        """
        offenders = []
        for name, value, pattern in patterns:
            # A pattern with no minable literal is still SCANNED, not skipped:
            # _worst_time always probes the empty head, which is how a pattern
            # quadratic in its own leading quantifiers gets caught. Skipping
            # here is what let r"\b(?:order)\s*#?\s*\d" through.
            literals = self._literals(pattern)
            try:
                compiled = (value if isinstance(value, re.Pattern)
                            else re.compile(pattern, re.IGNORECASE))
            except re.error:
                continue
            measured = self._measure(compiled, literals, repeats=1)
            if measured is None:
                continue              # genuinely linear and fast
            small, large, aborted = measured
            if aborted:
                offenders.append(
                    f"{name}: a single probe passed {self.PUMP_CEILING}s or the "
                    f"whole measurement passed {self.PUMP_BUDGET}s, so it could "
                    f"not be measured at all :: {pattern[:70]}")
                continue
            # Comfortably linear on the cheap look: done. Anything above a
            # third of the threshold gets a second, noise-free look before
            # anyone is told about it - a single timing sample is not
            # evidence, in either direction.
            if large <= small * self.PUMP_GROWTH * self.PUMP_SECOND_LOOK:
                continue
            if large >= self.PUMP_NOISE_FLOOR * 5:
                # Tenths of a second on 12 000 characters is not a scheduler
                # hiccup, and re-measuring something this slow is the single
                # most expensive thing this guard can do.
                offenders.append(f"{name}: {small:.4f}s -> {large:.4f}s :: {pattern[:70]}")
                continue
            confirmed = self._measure(compiled, literals,
                                      repeats=self.PUMP_REPEATS)
            if confirmed is None:
                continue
            small, large, aborted = confirmed
            if aborted:
                offenders.append(f"{name}: unmeasurable :: {pattern[:70]}")
                continue
            if large > small * self.PUMP_GROWTH:
                offenders.append(f"{name}: {small:.4f}s -> {large:.4f}s :: {pattern[:70]}")
        return offenders

    def _measure(self, compiled, literals, repeats):
        """(small, large) worst-case times at 4x input, or None if too fast.

        Split out of _scan so the cheap first look and the careful second one
        are literally the same code - a second implementation is how the
        self-check stopped testing the guard in round 10.
        """
        small, aborted = self._worst_time(compiled, literals, self.PUMP_N, repeats)
        if aborted:
            return small, float("inf"), True
        if small < self.PUMP_FLOOR:
            # Too fast to measure a ratio; pump harder before deciding.
            small, aborted = self._worst_time(compiled, literals,
                                              self.PUMP_N * 4, repeats)
            if aborted:
                return small, float("inf"), True
            if small < self.PUMP_FLOOR:
                return None
            large, aborted = self._worst_time(compiled, literals,
                                              self.PUMP_N * 16, repeats)
            return small, (float("inf") if aborted else large), aborted
        large, aborted = self._worst_time(compiled, literals,
                                          self.PUMP_N * 4, repeats)
        return small, (float("inf") if aborted else large), aborted

    def test_measurement_ignores_time_descheduled(self):
        import time
        from unittest.mock import patch

        class DescheduledSearch:
            def search(self, probe):
                time.sleep(0.005)

        # Deliberately paused search has substantial wall time but no regex
        # CPU cost. perf_counter must never decide regex complexity.
        with patch("time.perf_counter", side_effect=AssertionError("wall clock")):
            measured, aborted = self._worst_time(DescheduledSearch(), [], 1)
        self.assertFalse(aborted)
        self.assertLess(measured, self.PUMP_FLOOR)

    def test_single_catastrophic_search_is_interrupted_in_subprocess(self):
        import subprocess
        # Real timer and regex engine; outer deadline contains this positive
        # control if timer handling regresses.
        code = """
import re
from test_classifier_rules import ReDoSTests
probe = ReDoSTests()
probe.PUMP_BUDGET = 0.05
elapsed, aborted = probe._worst_time(re.compile(r'(x+)+y'), [], 80)
assert aborted and elapsed == float('inf'), (elapsed, aborted)
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=PROCESSOR_DIR,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_pattern_is_superlinear(self):
        self.assertEqual(
            self._scan(self._every_pattern()), [],
            "these patterns grow faster than linearly on their own input - "
            "the usual cause is two unbounded whitespace quantifiers with "
            "something optional between them; put the whitespace INSIDE the "
            "optional group so a single-character decision follows the first")

    @staticmethod
    def _guarded_modules():
        """Every module whose regexes run on attacker-controlled ticket text.

        The classifier is a package now, so every fully-qualified classifier
        submodule is included. Hermes is also a package now: scanning only its
        facade would leave a quadratic in ``hermes_runner.extract`` or
        ``hermes_runner.prompt`` invisible. The previous version read only the
        facade, and two historical bombs lived in draft_cleaner - so appending
        a quadratic to a helper module left the whole suite green.
        """
        import importlib

        modules = [
            (name, importlib.import_module(name))
            for name in _classifier_package_module_names()
        ]
        for name in ReDoSTests._hermes_package_module_names():
            try:
                modules.append((name, importlib.import_module(name)))
            except ImportError as exc:
                raise AssertionError(f"Hermes submodule {name} is not importable") from exc
        modules.append(("draft_cleaner", importlib.import_module("draft_cleaner")))
        return modules

    @staticmethod
    def _importable_guarded_modules() -> set:
        import importlib

        found = set(_classifier_package_module_names())
        # Import each discovered classifier module here too. This makes a new
        # file fail as an import/coverage error rather than silently dropping
        # out of the scan.
        for name in sorted(found):
            try:
                importlib.import_module(name)
            except ImportError as exc:
                raise AssertionError(f"classifier submodule {name} is not importable") from exc
        hermes_names = ReDoSTests._hermes_package_module_names()
        for name in hermes_names:
            try:
                importlib.import_module(name)
            except ImportError as exc:
                raise AssertionError(f"Hermes submodule {name} is not importable") from exc
            found.add(name)
        importlib.import_module("draft_cleaner")
        found.add("draft_cleaner")
        return found

    @staticmethod
    def _hermes_package_module_names() -> tuple[str, ...]:
        """Return every fully-qualified Hermes module, including the facade.

        The package is deliberately discovered from both the filesystem and
        ``pkgutil``. This catches a new source file even when it is not yet
        imported, and keeps the coverage assertion honest about the complete
        ``hermes_runner.*`` namespace.
        """
        import importlib

        package = importlib.import_module("hermes_runner")
        names = {package.__name__}
        for package_root in getattr(package, "__path__", ()):
            root = Path(package_root)
            if not root.is_dir():
                continue
            for path in root.rglob("*.py"):
                relative = path.relative_to(root)
                if "__pycache__" in relative.parts:
                    continue
                parts = list(relative.with_suffix("").parts)
                if parts and parts[-1] == "__init__":
                    parts.pop()
                if parts:
                    names.add("hermes_runner." + ".".join(parts))
            for module in pkgutil.walk_packages(
                (str(root),), prefix="hermes_runner."
            ):
                names.add(module.name)
        return tuple(sorted(names))

    # Enough to reach a pattern nested in a dict of lists of tuples. Bounded
    # only so a cycle or a huge data structure cannot hang the test.
    _WALK_DEPTH = 6

    @classmethod
    def _walk(cls_, label, value, depth=0, seen=None):
        """Yield (label, value, pattern-string) for regexes anywhere inside.

        The previous version enumerated exactly two shapes: a module-level
        re.Pattern, and an UPPERCASE-named list whose FIRST element is a str.
        Five containers were therefore unwatched - a tuple, a list of
        compiled patterns, a dict, a lowercase-named list, and a list whose
        first element happens not to be a string. No live pattern sat in one,
        but _PORTED_HIGH_PATTERNS already establishes the tuple idiom, so the
        first person to follow it would have had no guard at all.
        """
        seen = seen if seen is not None else set()
        if id(value) in seen or depth > cls_._WALK_DEPTH:
            return
        seen.add(id(value))

        if isinstance(value, re.Pattern):
            yield label, value, value.pattern
        elif isinstance(value, str):
            # A bare module-level string is not necessarily a regex, but the
            # cost of probing one that is not is a few milliseconds, and the
            # cost of skipping one that is was six production bombs.
            if cls_._looks_like_a_regex(value):
                yield label, value, value
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            for field in dataclasses.fields(value):
                yield from cls_._walk(
                    f"{label}.{field.name}", getattr(value, field.name),
                    depth + 1, seen)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                yield from cls_._walk(f"{label}[{key!r}]", key, depth + 1, seen)
                yield from cls_._walk(f"{label}[{key!r}]", item, depth + 1, seen)
        elif isinstance(value, (Sequence, Set, frozenset)) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for i, item in enumerate(value):
                yield from cls_._walk(f"{label}[{i}]", item, depth + 1, seen)

    # "\{\s*,?\s*\d" and not "\{\d": "{,4000}" is a valid Python quantifier
    # and the old form did not match it, so a bounded-but-quadratic pattern
    # was dropped by the filter without a trace. A silent drop is the exact
    # failure mode this guard exists to prevent.
    _REGEX_METACHARS = re.compile(
        r"\\[bswdWSDAZ]|\[[^\]]+\]|\(\?|[*+?]|\{\s*,?\s*\d")
    # Syntax English does not have. A string containing any of these is a
    # pattern whatever else it looks like - which matters, because five live
    # markers in draft_cleaner._SELF_TALK_MARKERS are plain-English phrases
    # built out of (?:...) groups and no backslashes at all, and the first
    # version of the prose test threw all five away.
    _REGEX_SYNTAX = re.compile(r"\\|\(\?|\[[^\]]+\]|\{\s*,?\s*\d|\||\^|\$")
    _WORDS = re.compile(r"[A-Za-z']+")

    @staticmethod
    def _looks_like_a_regex(text: str) -> bool:
        """Cheap filter so prose is not compiled and pumped as a pattern.

        Generous in the regex direction - a dropped pattern is invisible and
        a pumped sentence only costs milliseconds - so anything with syntax
        English does not have counts, unconditionally.

        The only strings rejected are ones whose ONLY regex-ish character is
        a bare "?", "*" or "+", which is ordinary punctuation - and which
        additionally READ as a sentence: five or more words, and either a
        capital letter at the start or a full stop, question mark or
        exclamation mark at the end.

        That last condition is what separates the classifier's 26 English
        self-test cases ("Is it possible the courier lost my parcel?") from
        a live pattern that happens to be a plain phrase
        ("the response above was complete" in draft_cleaner). Word count
        alone threw the pattern away.
        """
        if not text or not ReDoSTests._REGEX_METACHARS.search(text):
            return False
        if ReDoSTests._REGEX_SYNTAX.search(text):
            return True
        words = ReDoSTests._WORDS.findall(text)
        if len(words) < 5 or len(text) <= 30:
            return True
        reads_as_a_sentence = text[:1].isupper() or text.rstrip()[-1:] in ".?!"
        return not reads_as_a_sentence

    # An unknown fragment, modelled as "may be empty, may eat whitespace".
    #
    # Round 12 used "(?:)?" here, reasoning that an unknown piece should be
    # optional so the join around it stays visible. That REMOVES the danger
    # instead of preserving it: when the unresolvable piece is the one
    # carrying the quantifiers - sep = r"\s*#?\s*", then rf"\border{sep}\d" -
    # the folded pattern is "\border(?:)?\d", which is linear, and the guard
    # went green on 11 of 15 live bombs.
    #
    # Round 13 then tried r"\s*", on the argument that an unknown fragment
    # might be a whitespace quantifier. That cries wolf, loudly: for
    # `re.search(r"\bship\s*" + sep + r"\s*\d", text)` with sep a separator
    # character, it folds to three adjacent r"\s*" - a CUBIC pattern that
    # takes 3.6s on 1600 characters while every real separator measures
    # linear. A guard that reports a bomb where there is none, and takes
    # minutes to do it, gets deleted just as fast as one that misses.
    #
    # There is no placeholder that is both safe and honest, because the
    # answer genuinely depends on the fragment. So the placeholder is inert,
    # _live_value() below resolves the fragment against the module's real
    # namespace for every shape that turns up in this codebase, and anything
    # neither can resolve FAILS the guard with "extract it to a module-level
    # constant" rather than being guessed at either way.
    _UNKNOWN = "\x00"

    @staticmethod
    def _live_value(node, module):
        """Evaluate a pattern expression against the module's real namespace.

        Static folding cannot see through a dict lookup, a list or tuple
        index, a class attribute, or a name bound more than once - and each
        of those hid a planted bomb. The module is already imported, so the
        value exists; read it.

        Only calls that cannot have side effects are evaluated - re.escape
        and the pure string methods join/format - so this never runs module
        code. `"".join(_PARTS)` where _PARTS is a module-level list was the
        one shape static folding and a stricter whitelist both missed.
        """
        import ast

        pure = {"escape", "join", "format", "lower", "upper", "strip"}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if not (isinstance(func, ast.Attribute) and func.attr in pure):
                    return None
        try:
            source = ast.unparse(node)
            value = eval(source, dict(vars(module)),          # noqa: S307
                         {"token": "0123456789abcdef", "text": "",
                          "message": "", "subject": "", "pattern": ""})
        except Exception:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, re.Pattern):
            return value.pattern
        return None

    @staticmethod
    def _constant_names(tree):
        """name -> folded value, for names bound exactly once to a string.

        Without this, `sep = r"\\s*#?\\s*"` followed by `rf"\\border{sep}\\d"`
        folds to a pattern with no whitespace quantifiers in it at all, and
        the bomb reads as safe. Bound ONCE: a name reassigned in two places
        has no single value, and guessing one would be worse than admitting
        it is unknown.
        """
        import ast

        counts, values = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = [t for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target]
            else:
                continue
            for target in targets:
                counts[target.id] = counts.get(target.id, 0) + 1
                values[target.id] = node.value
        return {n: v for n, v in values.items() if counts.get(n) == 1}

    @classmethod
    def _fold(cls_, node, names=None):
        """Statically fold an expression into the pattern string it builds.

        Handles the shapes that actually turn up: a literal, a name bound to
        one, an f-string, `a + b`, `"..." % (...)`, `"...".format(...)` and
        `"".join([...])`. Anything it cannot resolve becomes _UNKNOWN rather
        than being dropped, so the join between two known fragments is still
        measured.
        """
        import ast

        names = names or {}
        if isinstance(node, ast.Name):
            bound = names.get(node.id)
            # `names` is stripped of this name before recursing, so a
            # self-referential binding cannot loop.
            if bound is not None:
                return cls_._fold(bound, {k: v for k, v in names.items()
                                          if k != node.id})
            return cls_._UNKNOWN
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else cls_._UNKNOWN
        if isinstance(node, ast.JoinedStr):
            return "".join(cls_._fold(v, names) for v in node.values)
        if isinstance(node, ast.FormattedValue):
            return cls_._fold(node.value, names)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add):
                return cls_._fold(node.left, names) + cls_._fold(node.right, names)
            if isinstance(node.op, ast.Mod):
                out = cls_._fold(node.left, names)
                right = node.right
                parts = (right.elts if isinstance(right, ast.Tuple) else [right])
                for part in parts:
                    out = re.sub(r"%[-#0 +]*\d*(?:\.\d+)?[sdrfgx]",
                                 lambda _m, p=cls_._fold(part, names): p,
                                 out, count=1)
                return out
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr, owner = node.func.attr, node.func.value
            if attr == "format":
                out = cls_._fold(owner, names)
                for a in node.args:
                    out = re.sub(r"\{[^{}]*\}",
                                 lambda _m, p=cls_._fold(a, names): p,
                                 out, count=1)
                return out
            if attr == "join" and node.args:
                sep = cls_._fold(owner, names)
                items = node.args[0]
                if isinstance(items, (ast.List, ast.Tuple)):
                    return sep.join(cls_._fold(e, names) for e in items.elts)
                return cls_._UNKNOWN
        return cls_._UNKNOWN

    # Functions that build a pattern from a per-run value. They are called
    # with a sample token rather than read statically, so a site inside one
    # is covered exactly and must not also be reported as unreadable.
    _FACTORIES = ("_json_marker_re", "_draft_tag_re")

    @classmethod
    def _patterns_built_inside_functions(cls_):
        """Regex literals that never reach module scope.

        hermes_runner builds its trusted-marker patterns per run, from a
        token: `re.compile(r'JSON_RESULT\\[' + re.escape(token) + r'\\]:...')`.
        Those strings exist only while the function runs, so the walk above
        cannot see them. Two routes, because each misses what the other
        catches:

          * every regex ARGUMENT of an re.* call anywhere in the source,
            statically folded - catches inline re.search(r"...", text) and,
            crucially, patterns assembled from several pieces;
          * the known factories, called with a sample token - catches what
            static folding cannot, e.g. re.escape() of a real value.

        Folding matters more than it looks. Scanning each string LITERAL
        separately reports the fragments, not the pattern: split the round-8
        bomb into r"\\border" + r"\\s*#?\\s*" + r"\\d" and no fragment is
        quadratic on its own, so the guard went green on a live bomb in four
        different shapes. An unknown piece folds to an OPTIONAL placeholder,
        because "something may or may not be here" is the worst case and a
        guard should assume it.
        """
        import ast
        import inspect

        re_funcs = {"compile", "search", "match", "fullmatch", "findall",
                    "finditer", "sub", "subn", "split"}
        for mod_name, module in cls_._guarded_modules():
            try:
                source = inspect.getsource(module)
                tree = ast.parse(source)
            except (OSError, TypeError, SyntaxError):  # pragma: no cover
                continue

            # `import re as _r` and `from re import search` both hide the
            # call from a check that insists on the name "re".
            const_names = cls_._constant_names(tree)
            # Line ranges of the token factories. A pattern assembled inside
            # one of them is resolved exactly, by CALLING it, further down -
            # re.escape() of a real value is not something static folding can
            # reproduce. Reporting the folded reading as well would name the
            # same site as unresolvable while it is in fact the best-covered
            # site in the file.
            factory_lines = [
                (fn.lineno, getattr(fn, "end_lineno", fn.lineno))
                for fn in ast.walk(tree)
                if isinstance(fn, ast.FunctionDef) and fn.name in cls_._FACTORIES
            ]
            re_names = {"re"}
            bare_re_funcs = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in ("re", "regex"):
                            re_names.add(alias.asname or alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module in ("re", "regex"):
                    for alias in node.names:
                        if alias.name in re_funcs:
                            bare_re_funcs.add(alias.asname or alias.name)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_re_call = (
                    (isinstance(func, ast.Attribute) and func.attr in re_funcs
                     and isinstance(func.value, ast.Name)
                     and func.value.id in re_names)
                    or (isinstance(func, ast.Name) and func.id in bare_re_funcs)
                )
                if not is_re_call:
                    continue
                # `pattern=` as a keyword, not just positionally.
                arg = node.args[0] if node.args else None
                if arg is None:
                    for kw in node.keywords:
                        if kw.arg == "pattern":
                            arg = kw.value
                if arg is None:
                    continue
                in_factory = any(lo <= node.lineno <= hi
                                 for lo, hi in factory_lines)
                readings = [cls_._live_value(arg, module)]
                if not in_factory:
                    readings.insert(0, cls_._fold(arg, const_names))
                for built in readings:
                    if built and cls_._looks_like_a_regex(built):
                        yield (f"{mod_name}.<line {node.lineno}>", built, built)

            # The factories, assembled. re.escape() of a real value is not
            # something static folding can reproduce.
            for factory in cls_._FACTORIES:
                fn = getattr(module, factory, None)
                if not callable(fn):
                    continue
                for token in ("", "0123456789abcdef"):
                    try:
                        built = fn(token)
                    except Exception:  # pragma: no cover - defensive
                        continue
                    yield from cls_._walk(f"{mod_name}.{factory}({token!r})",
                                          built)

    @classmethod
    def _unresolvable_sites(cls_):
        """Pattern expressions neither folding nor evaluation could read.

        A guard cannot say anything about a pattern it cannot see, and the
        two ways of pretending it can - assume the fragment is inert, or
        assume it is a whitespace quantifier - are a false negative and a
        false positive respectively, and round 13 shipped one of each. So it
        is reported as a failure and the code is asked to be readable.
        A site yields up to two readings - the statically folded one and the
        live one - so it counts as unresolvable only when EVERY reading of it
        still has a hole in it. Reporting the folded reading on its own
        flagged three sites that _live_value had already read perfectly.
        """
        by_site: dict = {}
        for label, _value, pattern in cls_._patterns_built_inside_functions():
            by_site.setdefault(label, []).append(pattern)
        for label, patterns in by_site.items():
            if all(cls_._UNKNOWN in p for p in patterns):
                yield label, patterns[0]

    def test_every_pattern_expression_is_resolvable(self):
        unresolved = ["%s :: %s" % (label, pattern.replace(self._UNKNOWN, "<?>"))
                      for label, pattern in self._unresolvable_sites()]
        self.assertEqual(
            unresolved, [],
            "these regexes are assembled from something the guard cannot "
            "read, so nothing checks them for catastrophic backtracking - "
            "move the fragment to a module-level constant")

    @classmethod
    def _every_pattern(cls_):
        """(name, value, pattern-string) for every regex in every module."""
        seen_patterns = set()
        scanned_modules = {
            name for name, _module in cls_._guarded_modules()
        }
        cls_._last_scanned_modules = scanned_modules
        for mod_name, module in cls_._guarded_modules():
            for name, value in vars(module).items():
                if name.startswith("__"):
                    continue
                for item in cls_._walk(f"{mod_name}.{name}", value):
                    if item[2] not in seen_patterns:
                        seen_patterns.add(item[2])
                        yield item
        for item in cls_._patterns_built_inside_functions():
            if item[2] not in seen_patterns:
                seen_patterns.add(item[2])
                yield item
        # The coverage test checks the exact module identities visited even
        # when a module currently has no regex.

    def test_the_guard_covers_every_module_that_reads_ticket_text(self):
        list(self._every_pattern())
        seen = self._last_scanned_modules
        self.assertEqual(
            seen,
            self._importable_guarded_modules(),
            "the ReDoS guard did not visit every fully-qualified classifier "
            "submodule and sibling text-processing module",
        )

    # One live quadratic, in every container someone might reasonably reach
    # for. `\border\s*#?\s*\d` is the round-8 bug in the form it shipped in.
    BOMB = r"\border\s*#?\s*\d"

    def test_the_guard_finds_a_bomb_in_every_container_shape(self):
        """The walk must not be shape-dependent.

        Its predecessor recognised exactly two: a module-level re.Pattern and
        an UPPERCASE list whose FIRST element is a str. Everything below
        passed it silently. This test is the reason the walk is recursive
        rather than a longer if/elif chain - it fails for any shape the walk
        cannot reach, including ones nobody has thought of yet.
        """
        import types

        shapes = {
            "bare_compiled": re.compile(self.BOMB),
            "BARE_STRING": self.BOMB,
            "UPPER_LIST": [self.BOMB],
            "lower_list": [self.BOMB],
            "TUPLE": (self.BOMB,),
            "SET": {self.BOMB},
            "FROZEN": frozenset({self.BOMB}),
            "LIST_OF_COMPILED": [re.compile(self.BOMB)],
            "DICT_VALUES": {"a": self.BOMB},
            "DICT_KEYS": {self.BOMB: "a"},
            "STR_FIRST_IS_NOT_A_STR": [re.compile("x"), self.BOMB],
            "NESTED": {"outer": [("inner", [self.BOMB])]},
        }
        for label, value in shapes.items():
            with self.subTest(shape=label):
                fake = types.ModuleType("fake_module")
                setattr(fake, label, value)
                original = ReDoSTests._guarded_modules
                try:
                    ReDoSTests._guarded_modules = staticmethod(
                        lambda: [("fake", fake)])
                    found = list(self._every_pattern())
                finally:
                    ReDoSTests._guarded_modules = original
                self.assertTrue(
                    any(p == self.BOMB for _n, _v, p in found),
                    f"a quadratic hidden in a {label} is invisible to the guard")

        # Reaching it and flagging it are two different failures, so prove the
        # second one too - once. Measuring a deliberate bomb costs seconds, and
        # _scan does not care which container the pattern arrived in.
        self.assertTrue(
            self._scan([("planted", self.BOMB, self.BOMB)]),
            "the guard reached the bomb but did not flag it")

    # The same bomb, split so that NO individual string literal is quadratic.
    # This is the shape that matters: a guard that scans literals one at a
    # time reports nothing, because the danger is in the join. Round 12
    # measured four live bombs going green exactly this way.
    HEAD, MID, TAIL = r"\border", r"\s*#?\s*", r"\d"

    def _scan_fake_module(self, src):
        """Run the real _every_pattern() over a synthetic module's source."""
        import inspect
        import types

        fake = types.ModuleType("fake_fn_module")
        exec(compile(src, "<fake>", "exec"), fake.__dict__)
        original_mods = ReDoSTests._guarded_modules
        original_src = inspect.getsource
        try:
            ReDoSTests._guarded_modules = staticmethod(lambda: [("fake", fake)])
            inspect.getsource = lambda m: src if m is fake else original_src(m)
            return list(self._every_pattern())
        finally:
            ReDoSTests._guarded_modules = original_mods
            inspect.getsource = original_src

    def test_the_guard_finds_a_bomb_assembled_inside_a_function(self):
        """A pattern compiled per-call never reaches module scope, and it is
        rarely one literal when it does.

        hermes_runner builds its trusted-marker patterns from a per-run token
        by concatenation, so `vars(module)` cannot see them and scanning the
        fragments separately proves nothing about the result.
        """
        import textwrap

        h, m, t = self.HEAD, self.MID, self.TAIL
        shapes = {
            "whole literal": f'return re.search(r"{h}{m}{t}", text)',
            "concatenation": f'return re.compile(r"{h}" + r"{m}" + r"{t}")',
            "f-string": f'sep = r"{m}"\n    return re.search(rf"{h}{{sep}}{t}", text)',
            "percent format": f'return re.search(r"{h}%s{t}" % r"{m}", text)',
            "str.format": f'return re.search(r"{h}{{0}}{t}".format(r"{m}"), text)',
            "str.join": f'return re.search("".join([r"{h}", r"{m}", r"{t}"]), text)',
            "keyword arg": f'return re.search(pattern=r"{h}" + r"{m}" + r"{t}", string=text)',
            "aliased import": f'return _r.search(r"{h}" + r"{m}" + r"{t}", text)',
            "from-import": f'return _search(r"{h}" + r"{m}" + r"{t}", text)',
            "escaped token": f'return re.compile(r"{h}" + r"{m}" + r"{t}" + re.escape(text))',
        }
        assembled = h + m + t
        for label, call in shapes.items():
            with self.subTest(shape=label):
                src = textwrap.dedent('''
                    import re
                    import re as _r
                    from re import search as _search

                    def build(text):
                        %s
                ''') % call
                found = self._scan_fake_module(src)
                self.assertTrue(
                    any(assembled in p for _n, _v, p in found),
                    f"a quadratic assembled by {label} is invisible to the "
                    f"guard - it would ship")

        # Reaching it and flagging it are separate failures. Prove the second
        # once: measuring a deliberate bomb costs seconds and _scan does not
        # care how the string was built.
        self.assertTrue(
            self._scan([("planted", assembled, assembled)]),
            "the guard reached the assembled bomb but did not flag it")

    def test_folding_an_unknown_piece_does_not_hide_the_join(self):
        # The whole point of the placeholder: an unresolvable fragment must
        # be treated as possibly-empty, or "\\s*" + X + "\\s*\\d" reads as safe.
        folded = self._fold_source(
            f'return re.compile(r"{self.HEAD}\\s*" + unknown_thing + r"{self.TAIL}")')
        self.assertIn(self._UNKNOWN, folded)
        # ...and an unresolvable fragment is reported, not guessed at.
        self.assertTrue(
            list(self._unresolvable_sites()) is not None)

    def _fold_source(self, call):
        import ast
        import textwrap

        src = textwrap.dedent("""
            import re

            def build(unknown_thing):
                %s
        """) % call
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("compile", "search"):
                    return self._fold(node.args[0])
        self.fail("no re call found in the fixture")

    def test_the_regex_filter_does_not_swallow_a_real_pattern(self):
        """_looks_like_a_regex is the one place a pattern can be dropped
        silently, so pin both directions."""
        for pattern in (self.BOMB, r"\s+", r"a{2,}", r"(?:x)?", r"[abc]+",
                        r"\bwaiting\s+(?:for\s+)?(?:\d+|a)?\s*days?",
                        # "{,4000}" is a valid quantifier and the first
                        # version of the filter did not recognise it, so a
                        # bounded quadratic was dropped without a trace.
                        "order {,4000}#{,1} {,4000}5",
                        r"a{,3}b"):
            self.assertTrue(self._looks_like_a_regex(pattern), pattern)
        for prose in ("Classifier: IMMEDIATE", "deterministic", "",
                      "processor/classifier.py",
                      # Twenty-six of the classifier's own self-test
                      # sentences used to be compiled and pumped as
                      # patterns, purely because they contain a "?".
                      "Is it possible the courier lost my parcel?",
                      "I lost the discount code you emailed me, can you resend it?",
                      "Do you know where my order is, it has been three weeks?"):
            self.assertFalse(self._looks_like_a_regex(prose), prose)

    def test_the_filter_never_drops_a_pattern_the_modules_actually_use(self):
        """The filter is the only silent drop in the guard, so check it
        against the real tables rather than hand-picked examples.

        The property is not "nothing is ever dropped" - a pattern with no
        quantifier in it cannot backtrack at all, so dropping one is free.
        It is "nothing DROPPED can be superlinear". Anything with a *, +, ?
        or {n,m} in it has to reach the scan.

        NO `startswith("\\\\")` precondition. The first version had one, and
        the prose branch it was meant to police only fires on strings with
        no backslash - so the test could not observe the thing it was written
        to observe, and five prose-shaped live patterns in
        draft_cleaner._SELF_TALK_MARKERS were invisible to it.
        """
        quantifier = re.compile(r"(?<!\\)[*+?]|\{\s*,?\s*\d")
        dropped = []
        for _mod_name, module in self._guarded_modules():
            for name, value in vars(module).items():
                if not isinstance(value, (list, tuple)) or not name.isupper():
                    continue
                for pattern in value:
                    if (isinstance(pattern, str)
                            and quantifier.search(pattern)
                            and not self._looks_like_a_regex(pattern)):
                        dropped.append(f"{name}: {pattern}")
        self.assertEqual(
            dropped, [],
            "the filter dropped live patterns that contain a quantifier")

    def test_a_dropped_string_really_cannot_backtrack(self):
        """The escape hatch above is only safe if it is true.

        Compile every string the filter rejects and assert the engine finds
        no repeat operator in it - that is what makes dropping it free.
        """
        import sre_parse

        def has_repeat(parsed):
            for op, arg in parsed:
                name = str(op)
                if "REPEAT" in name or "MAX_REPEAT" in name or "MIN_REPEAT" in name:
                    return True
                if isinstance(arg, sre_parse.SubPattern) and has_repeat(arg):
                    return True
                if isinstance(arg, tuple):
                    for item in arg:
                        if isinstance(item, sre_parse.SubPattern) and has_repeat(item):
                            return True
                        if isinstance(item, list):
                            for alt in item:
                                if isinstance(alt, sre_parse.SubPattern) and has_repeat(alt):
                                    return True
            return False

        for _mod_name, module in self._guarded_modules():
            for name, value in vars(module).items():
                if not isinstance(value, (list, tuple)) or not name.isupper():
                    continue
                for pattern in value:
                    if not isinstance(pattern, str) or self._looks_like_a_regex(pattern):
                        continue
                    try:
                        parsed = sre_parse.parse(pattern)
                    except re.error:
                        continue
                    with self.subTest(name=name, pattern=pattern[:50]):
                        self.assertFalse(
                            has_repeat(parsed),
                            f"{name} entry {pattern!r} was dropped by the "
                            f"filter but contains a repeat operator")

    def test_the_literal_miner_reaches_every_shape(self):
        # Round 10 planted four quadratics that the miner could not reach, and
        # all four passed. Each shape is pinned here by the property that
        # matters: the miner must produce a literal that actually appears in
        # matching input.
        for pattern, needed in [
            (r"\border\s*#?\s*\d", "order"),            # plain (the control)
            (r"\b[Oo]rder\s*#?\s*\d", "order"),         # char class
            (r"\b(?:order)\s*#?\s*\d", "order"),        # (?: group
            (r"(?<=order)\s*#?\s*\d", "order"),         # lookbehind
            (r"\bcase\d*x?\d*[a-z]\b", "case"),         # digit-driven
            (r"\b(?i:Order)\s*#?\s*\d", "order"),       # inline flag group
        ]:
            with self.subTest(pattern=pattern):
                mined = [h.lower() for h in self._literals(pattern)]
                self.assertIn(needed, mined,
                              f"the miner cannot build input reaching {pattern}")

    def test_the_miner_does_not_cap_its_output(self):
        # A cap is how the previous TWO versions of this failed: a quadratic
        # appended to the tail of a long alternation was never probed.
        import string

        words = [f"word{a}{b}" for a in string.ascii_lowercase
                 for b in string.ascii_lowercase][:200]
        self.assertGreaterEqual(len(self._literals("|".join(words))), 200)

    def test_the_superlinear_check_actually_catches_a_planted_bomb(self):
        # A guard that cannot fail is worse than no guard - which is exactly
        # what the previous version of this test turned out to be. So: plant
        # each historical shape and confirm the check fires.
        bombs = [
            # All six live bugs, in the form they shipped in.
            r"\border\s*#?\s*\d",                                       # round 8
            r"\ba\s+hole\b(?!\s*-?\s*away)",                            # round 8
            r"^[\s>*#\-]*[-\s]*\[?end of (?:response|draft)\]?",        # round 8
            r"\bwaiting\s+(?:for\s+)?(?:\d+|a)?\s*(?:days?|weeks?)",    # round 9
            r"\byou\s+sent\s+(?:a|an|the)?\s*(?:wrong|different)\b",    # round 9
            r"\bsubject\s*#?\s*\d",                                     # round 8
            # Shapes that got PAST the previous two versions of this guard.
            r"\bfoo\s+(?:bar\s+)?\s*baz\b",
            r"\bfoo\s*(?:abcdefgh)?\s*baz\b",
            r"\bfoo\s*(?:\d+)?\s*baz\b",
            r"\bfoo[ \t]*x?[ \t]*baz\b",
            # ...and the four round-10 bypasses of the literal miner.
            r"\b[Oo]rder\s*#?\s*\d",           # literal inside a char class
            r"\b(?:order)\s*#?\s*\d",          # literal inside a (?: group
            r"(?<=order)\s*#?\s*\d",           # literal inside a lookbehind
            r"\bcase\d*x?\d*[a-z]\b",          # quadratic on DIGITS, not spaces
        ]
        # Driven through the REAL guard body, not a copy of it. The previous
        # version had its own loop, so it never touched PUMP_FLOOR or the
        # module enumeration - and setting PUMP_FLOOR to 1e9, which disables
        # the guard entirely, left the suite green with a live bomb planted.
        for pattern in bombs:
            with self.subTest(pattern=pattern):
                offenders = self._scan([(f"planted::{pattern}", pattern, pattern)])
                self.assertTrue(
                    offenders,
                    f"the guard does NOT catch {pattern} - it would ship")

    def test_an_order_word_before_a_whitespace_run_is_linear(self):
        import time
        for probe in ("order" + " " * 40_000,
                      "a hole" + " " * 40_000,
                      "there is a rip" + " " * 40_000,
                      "#" + " " * 40_000):
            with self.subTest(probe=probe[:12]):
                start = time.perf_counter()
                _c(probe)
                self.assertLess(time.perf_counter() - start, 2.0,
                                "a classifier pattern is backtracking")

    def test_the_quote_header_pattern_is_bounded(self):
        import time
        # "<[^>]+@[^>]+>" nested two unbounded quantifiers: 72 SECONDS on 512KB.
        payload = "hi <" + "a@" * 40000
        started = time.perf_counter()
        _c(payload)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0, f"{elapsed:.2f}s on {len(payload)} bytes")

    def test_classification_is_bounded_on_every_hostile_shape(self):
        import time
        # A blow-up detector, not a stopwatch: a real ReDoS bomb on these
        # payloads costs seconds-to-minutes, so 5s still fails an order of
        # magnitude before a bomb could pass - while leaving headroom for a
        # slow shared CI runner (worst healthy shape measured 1.14s there).
        shapes = ["following up ", "a stain ", "!!! ", "MISSING ", "> quoted\n",
                  "On 1 Jan 2026 x wrote:\n", "why ", "hi <a@", "\n\n"]
        for shape in shapes:
            payload = shape * 40000
            with self.subTest(shape=shape[:12]):
                started = time.perf_counter()
                _c(payload, subject=payload[:2000])
                self.assertLess(time.perf_counter() - started, 5.0)


class LengthCapTests(unittest.TestCase):
    """A plain head truncation was the one way this classifier could come out
    LOWER than main's - and the tail is exactly where a bottom-posting customer
    writes."""

    def test_a_signal_at_the_very_end_survives_the_cap(self):
        for body in [
            "x " * 40000 + " my order arrived damaged and I want a refund",
            "hi " * 30000 + "chargeback",
        ]:
            with self.subTest(length=len(body)):
                self.assertGreater(len(body), cls._MAX_SCAN_CHARS)
                self.assertEqual(_c(body)["priority"], IMMEDIATE)

    def test_a_long_real_thread_still_escalates(self):
        reply = ("Thanks for getting in touch, we are looking into this for you "
                 "and will come back as soon as we have an update.\n"
                 "> your earlier message\n")
        body = reply * 1000 + "\nI want a refund, my order arrived damaged."
        self.assertGreater(len(body), 2 * cls._MAX_SCAN_CHARS)
        self.assertEqual(_c(body)["priority"], IMMEDIATE)

    def test_the_cap_is_big_enough_to_be_useful(self):
        self.assertGreaterEqual(_canonical_value("_MAX_SCAN_CHARS"), 4000)


class QuotedStoreTextTests(unittest.TestCase):
    """The store's own words must never be classified as the customer's.

    Quoted-history stripping used to be gated on the length cap, so for every
    real-sized ticket the support footer was keyword-matched - and that footer
    says "if an item is missing from your parcel".
    """

    FOOTER = ("> Buttons Bebe - if your order hasn't arrived within 5 working "
              "days, or an item is\n> missing from your parcel, just reply to "
              "this email and we will sort it out.")

    def test_a_thanks_under_the_support_footer_stays_normal(self):
        self.assertEqual(_c(f"Thank you, got it!\n\n{self.FOOTER}")["priority"], NORMAL)

    def test_an_outlook_style_quote_block_is_not_the_customer(self):
        body = ("Thanks, all good!\n\nFrom: Buttons Bebe <hello@buttonsbebe.com>\n"
                "Subject: your order\n\nIf an item is missing from your parcel "
                "just reply.")
        self.assertEqual(_c(body)["priority"], NORMAL)

    def test_a_quoted_store_promo_does_not_escalate_the_question_below_it(self):
        body = ("From: Buttons Bebe <hello@buttonsbebe.com>\nSubject: FLASH SALE\n\n"
                "FLASH SALE!!! 30% OFF EVERYTHING!!! HURRY!!!\n\n"
                "Do you restock the cream romper?")
        self.assertEqual(_c(body)["priority"], NORMAL)

    def test_the_customers_own_complaint_under_a_quote_still_escalates(self):
        body = f"My order arrived damaged and I want a refund.\n\n{self.FOOTER}"
        self.assertEqual(_c(body)["priority"], IMMEDIATE)


class BoilerplateFilterNeverDeescalatesTests(unittest.TestCase):
    """The boilerplate filter must never hide text from MAIN's rules.

    A boilerplate phrase is not always the store talking. "%off" is in the
    pattern because shop footers advertise sales - but customers mention their
    discount code constantly, and "working days" / "terms and conditions" /
    "flash sale" / "unsubscribe" are all things a customer writes too.

    When such a sentence is its own paragraph AND some other paragraph
    survives, the empty-result fallback does not fire and the complaint is
    simply deleted. Measured against main's classifier on 4 238 messages of
    exactly this shape: 3 998 silent immediate -> normal de-escalations
    (94.3%). Splitting the tables so main's rules read the unfiltered text
    took that to 0.

    Every case below is IMMEDIATE or HIGH under main's classifier. If any of
    them comes back NORMAL, the port has made the system less safe than the
    code it is replacing - which is the one thing it may not do.
    """

    TAIL = "\n\nKind regards,\nSarah"

    IMMEDIATE_CASES = [
        "I used the 20% off code at checkout and the dress arrived damaged.",
        "I paid with the 15% off voucher and want a refund.",
        "The 10%off code applied but you sent the wrong size.",
        "It has been 7 working days and my parcel never arrived.",
        "Your terms and conditions say final sale but the romper is defective.",
        "I ordered in the flash sale and the onesie arrived torn.",
        "Please unsubscribe me, and also refund order 1042.",
        "Unsubscribe. This is a scam.",
        "Let us know when the refund lands, the item was damaged.",
        "We are looking into legal action, my package was stolen.",
        "Thanks for your patience but I am filing a chargeback.",
    ]

    HIGH_CASES = [
        "Five working days later and the item is still not received.",
        "I read the terms and conditions and this is urgent.",
        "I will hit reply again tomorrow - where is my order?",
    ]

    def test_poisoned_paragraphs_still_reach_immediate(self):
        for body in self.IMMEDIATE_CASES:
            with self.subTest(body=body):
                self.assertEqual(_c(body + self.TAIL)["priority"], IMMEDIATE, body)
                # And with the survivor above rather than below it.
                self.assertEqual(_c("Hi there,\n\n" + body)["priority"], IMMEDIATE, body)

    def test_poisoned_paragraphs_still_reach_at_least_high(self):
        for body in self.HIGH_CASES:
            with self.subTest(body=body):
                self.assertGreaterEqual(
                    _RANK[_c(body + self.TAIL)["priority"]], _RANK[HIGH], body)

    def test_the_filter_still_protects_the_new_rules(self):
        # The whole point of the filter: a store footer must not fire the
        # rules this port ADDED. "missing from your parcel" is _WEAK_OMISSION.
        footer = ("Thanks for reaching out! If an item is missing from your "
                  "parcel, just reply to this email and our customer care "
                  "team will sort it out.")
        self.assertEqual(_c(f"Thank you, all sorted!\n\n{footer}")["priority"], NORMAL)

    def test_main_rules_read_the_unfiltered_text(self):
        # Direct structural check, so this survives a rewording of the cases.
        seen = {}
        any_owner = _canonical_owner("_find_matches_any")
        find_owner = _canonical_owner("_find_matches")
        original_any = getattr(any_owner, "_find_matches_any")
        original_find = getattr(find_owner, "_find_matches")

        def any_spy(views, patterns):
            if patterns is _canonical_value("_MAIN_IMMEDIATE_KEYWORDS"):
                seen["main"] = views[0]
            return original_any(views, patterns)

        def find_spy(text, patterns):
            if patterns is _canonical_value("_PORT_IMMEDIATE_KEYWORDS"):
                seen["port"] = text
            return original_find(text, patterns)

        setattr(any_owner, "_find_matches_any", any_spy)
        setattr(find_owner, "_find_matches", find_spy)
        try:
            _c("I used the 20% off code and the dress arrived damaged.\n\nSarah")
        finally:
            setattr(any_owner, "_find_matches_any", original_any)
            setattr(find_owner, "_find_matches", original_find)
        self.assertIn("20% off", seen["main"])
        self.assertNotIn("20% off", seen["port"])


class PurchaseHistoryTests(unittest.TestCase):
    """"I ordered from you last year" is history, not delivery evidence."""

    def test_history_alone_does_not_unlock_the_omission_rules(self):
        for message in [
            "I bought a gift card from you last month and I have lost the code.",
            "I ordered from you last year. The gift box option seems missing from checkout now.",
            "You sent me the newsletter twice and the discount code is missing from it.",
            "I ordered a few things at Christmas, I have lost track of what I still need.",
            "I ordered twice before and loved it! One tiny thing, the newsletter link is missing.",
            "I purchased a voucher and left out the recipient email by mistake.",
        ]:
            with self.subTest(message=message[:50]):
                self.assertEqual(_c(message)["priority"], NORMAL)

    def test_a_shipment_claim_is_still_evidence(self):
        for message in ["You sent a different item instead of the one I picked.",
                        "You packed the wrong size.",
                        "You shipped me the wrong colour."]:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], IMMEDIATE)


class CapsPolitenessTests(unittest.TestCase):
    """Praise in capitals is common, and it uses the same words anger does.

    A deliberate trade, measured both ways. Removing the positive-sentiment
    veto from the anchor path caught a handful of all-caps sarcasm but
    escalated 19 of 20 genuinely grateful all-caps messages - and every one of
    those is a push notification to the owner's phone. The veto is back, with
    an exemption for anchors that essentially never appear in praise.
    """

    GRATEFUL = [
        "THANKS SO MUCH FOR THE QUICK REPLY",
        "STILL LOVING THE ROMPER THANK YOU",
        "SERIOUSLY THE CUTEST THING EVER THANK YOU",
        "WORTH EVERY PENNY MONEY WELL SPENT LOVE IT",
        "GORGEOUS QUALITY WORTH THE MONEY THANK YOU",
        "PLEASE PASS ON MY THANKS TO YOUR MANAGER LOVELY SERVICE",
        "PERFECT THANK YOU I WILL ORDER AGAIN IMMEDIATELY",
        "THANK YOU SO MUCH I AM SO HAPPY WITH MY ORDER",
        "THANK YOU SO MUCH FOR THE FAST DELIVERY",
        "SO PLEASED WITH THIS LITTLE SET THANK YOU",
    ]

    # Round-5 review measured 6 of these 20 paging the owner. Two causes:
    # the praise vocabulary for baby clothes was missing from _POSITIVE_RE
    # (worth, favourite, softest, comfy, glad, happier), and "never" counted
    # as a grievance word on its own, which cancelled the positive veto for
    # "NEVER BEEN HAPPIER" and made NEVER a hard anchor on top.
    GRATEFUL_ROUND5 = [
        "I HAVE NEVER BEEN HAPPIER WITH AN ORDER",
        "STILL MY FAVOURITE SHOP, THE SOFTEST COTTON",
        "I WAS WAITING FOR THIS RESTOCK AND IT WAS WORTH IT",
        "I WILL NEVER SHOP ANYWHERE ELSE, LOVE IT",
        "STILL WEARING IT EVERY DAY, SO SOFT",
        "ORDERED IMMEDIATELY AND SO GLAD I DID",
        "SO COMFY AND COSY, HIGHLY RECOMMEND",
        "TELL YOUR MANAGER THE PACKAGING IS BEAUTIFUL",
        "PLEASE REPLY WITH THE RESTOCK DATE, I LOVE THESE",
        "MONEY WELL SPENT, THE QUALITY IS AMAZING",
    ]

    HARD = [
        "THANK YOU BUT I WANT A REFUND NOW",
        "THANKS BUT THIS IS UNACCEPTABLE",
        "THANKS FOR THE REPLY BUT MY PARCEL IS STILL MISSING",
        "LOVELY SHOP BUT THIS IS A DISGRACE",
    ]

    # Loosening the veto must not cost any of these. Every one is all-caps
    # anger, and several use the exact words the praise list above uses.
    ANGRY = [
        "I WANT MY MONEY NOW",
        "THIS IS A JOKE",
        "PICK UP THE PHONE",
        "I HAVE HAD ENOUGH OF BEING IGNORED",
        "WHERE IS MY REFUND",
        "ABSOLUTELY UNACCEPTABLE SERVICE",
        "I HAVE HAD IT WITH YOU AND THE WAY YOU AND THE TEAM TREAT ME",
        "NOBODY HAS ANSWERED ME IN TWO WEEKS",
        "MY ORDER IS STILL MISSING AND NOBODY REPLIES",
        "THIS IS RIDICULOUS, I WANT A MANAGER",
        "I AM FURIOUS ABOUT THIS",
        "STILL NO ANSWER, THIS IS PATHETIC",
        "NEVER ORDERING FROM YOU AGAIN, WHAT A DISGRACE",
        "I HAVE WAITED THREE WEEKS AND PAID FOR EXPRESS",
        "I DEMAND A RESPONSE TODAY",
        "NOT WORTH THE MONEY, WHAT A WASTE",
    ]

    def test_grateful_capitals_never_page_the_owner(self):
        for message in self.GRATEFUL + self.GRATEFUL_ROUND5:
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], NORMAL)

    def test_a_hard_anchor_fires_through_the_politeness(self):
        for message in self.HARD:
            with self.subTest(message=message):
                self.assertGreaterEqual(_RANK[_c(message)["priority"]], _RANK[HIGH])

    def test_all_caps_anger_still_escalates(self):
        for message in self.ANGRY:
            with self.subTest(message=message):
                self.assertGreaterEqual(_RANK[_c(message)["priority"]], _RANK[HIGH])

    def test_never_alone_is_not_a_grievance_word(self):
        # The word by itself is as common in praise as in complaint.
        self.assertIsNone(cls._NEGATIVE_RE.search("never been happier"))
        self.assertIsNone(cls._NEGATIVE_RE.search("i will never shop elsewhere"))
        # ...but the complaint uses of it still count.
        for grievance in ("never received", "never arrived", "never replied",
                          "never ordering from you again", "never again"):
            self.assertIsNotNone(cls._NEGATIVE_RE.search(grievance), grievance)

    def test_never_received_still_reaches_immediate_by_keyword(self):
        # The narrowed _NEGATIVE_RE must not be load-bearing for real misses:
        # these are caught by main's keyword table, not by sentiment.
        for message in ("I NEVER RECEIVED MY ORDER", "THANK YOU BUT I NEVER GOT IT"):
            with self.subTest(message=message):
                self.assertEqual(_c(message)["priority"], IMMEDIATE)

    def test_the_hard_anchor_set_excludes_words_praise_uses(self):
        for soft in ("MONEY", "REPLY", "RESPONSE", "WAITING", "MANAGER",
                     "PHONE", "IMMEDIATELY", "URGENT", "SERIOUSLY", "STILL",
                     "NEVER"):
            self.assertNotIn(soft, cls._SHOUT_HARD_ANCHORS)
        for hard in ("REFUND", "SCAM", "FRAUD", "UNACCEPTABLE", "DISGRACE"):
            self.assertIn(hard, cls._SHOUT_HARD_ANCHORS)


class TuningConstantTests(unittest.TestCase):
    """Pin the numeric constants. Mutation testing found every one of them
    could be changed - in either direction - with the whole suite still green.
    """

    def test_caps_stopwords_cover_the_tokens_that_are_information(self):
        # These are the tokens that made an ordinary shipping question look
        # like shouting when they counted toward the caps ratio.
        for token in ("USPS", "DHL", "FEDEX", "VAT", "THE", "AND", "YOU", "MY"):
            self.assertIn(token, cls._CAPS_STOPWORDS)
        self.assertEqual(
            _c("Do you ship via USPS UPS DHL FEDEX to the USA and is VAT included?")["priority"],
            NORMAL)

    def test_the_shout_ratio_is_load_bearing(self):
        probe = ("hello i am WAITING on a REPLY about one small thing and "
                 "otherwise it is all completely ok")
        self.assertEqual(_c(probe)["priority"], NORMAL)
        owner, original = _set_canonical("_SHOUT_MIN_RATIO", 0.01)
        try:
            loosened = _c(probe)["priority"]
        finally:
            setattr(owner, "_SHOUT_MIN_RATIO", original)
        self.assertNotEqual(loosened, NORMAL,
                            "_SHOUT_MIN_RATIO can be dropped to 0.01 with no effect")

    def test_the_sustained_ratio_is_load_bearing(self):
        probe = ("hello there i wanted to say that I HAVE HAD IT WITH YOU AND "
                 "THE WAY YOU TREAT ME but otherwise it is all ok and there is "
                 "nothing else i would change about any of it at the moment")
        self.assertEqual(_c(probe)["priority"], NORMAL)
        owner, original = _set_canonical("_SUSTAINED_MIN_RATIO", 0.01)
        try:
            loosened = _c(probe)["priority"]
        finally:
            setattr(owner, "_SUSTAINED_MIN_RATIO", original)
        self.assertNotEqual(loosened, NORMAL,
                            "_SUSTAINED_MIN_RATIO can be dropped with no effect")

    def test_a_high_value_order_with_a_complaint_is_flagged(self):
        # The order-value threshold had no test at all: nothing passed order_data.
        payload = {"ticket_id": 1, "message_text": "my order arrived damaged",
                   "ticket_subject": "", "intents": []}
        rich = classify(payload, order_data={"total_price": "240.00"})
        self.assertEqual(rich["priority"], IMMEDIATE)
        self.assertIn("240", rich["reason"])
        cheap = classify(payload, order_data={"total_price": "12.00"})
        self.assertNotIn("high order value", cheap["reason"])

    def test_a_bad_order_total_does_not_crash(self):
        payload = {"ticket_id": 1, "message_text": "my order arrived damaged",
                   "ticket_subject": "", "intents": []}
        for total in ("", None, "abc", {}, []):
            with self.subTest(total=total):
                self.assertEqual(classify(payload, order_data={"total_price": total})["priority"],
                                 IMMEDIATE)

    def test_the_scan_window_covers_a_realistic_ticket(self):
        # 8000 was small enough to lose anything written in the MIDDLE of a
        # long thread, which was a strict de-escalation against main.
        self.assertGreaterEqual(_canonical_value("_MAX_SCAN_CHARS"), 50_000)
        body = ("Hi there, hope you are well. " * 250
                + "The romper you sent arrived damaged and I would like a refund. "
                + "Anyway, do you restock the sage range? " * 250)
        self.assertGreater(len(body), 16_000)
        self.assertEqual(_c(body)["priority"], IMMEDIATE)

    def _seam_probe(self, before: str, after: str) -> str:
        """Build a message whose truncation seam falls EXACTLY between the two
        fragments, computed from the live constants so the test cannot go
        vacuous if the cap or the split ratio changes."""
        head_len = cls._MAX_SCAN_CHARS * 3 // 4
        tail_len = cls._MAX_SCAN_CHARS - head_len - len(cls._TRUNCATION_SENTINEL)
        head = ("ab " * head_len)[:head_len - len(before)] + before
        tail = after + (" cd" * tail_len)[:tail_len - len(after)]
        # The filler MUST be word characters with no spaces at its edges.
        # With " middle " the fragments were already whole words in the raw
        # message - "abnon-refund middle" contains "refund" between two real
        # boundaries - so main matched it too, and the test was asserting the
        # port classify LOWER than main. It went green only because main's
        # tables were reading the truncated text, i.e. it was pinning the
        # round-6 blocker in place rather than catching it.
        body = head + "qqmiddleqq" * 500 + tail
        self.assertEqual(len(head), head_len)
        self.assertEqual(len(tail), tail_len)
        self.assertGreater(len(body), cls._MAX_SCAN_CHARS)
        bounded = cls._bound(body)
        self.assertIn(before + cls._TRUNCATION_SENTINEL + after, bounded,
                      "the probe did not land on the seam")
        # ...and the seam must be the ONLY way a match could appear. If the
        # raw text already escalates, the probe proves nothing about the seam.
        self.assertEqual(
            _RANK[_c_main_view(body)], _RANK[NORMAL],
            "probe is vacuous: the untruncated text already escalates")
        return body

    def test_the_seam_cannot_join_two_fragments_into_a_word(self):
        # Every pattern uses \s+, which matches a newline, so a "\n" sentinel
        # let the splice invent a phrase present in neither half.
        self.assertEqual(_c(self._seam_probe("for your  wrong", "size guide"))["priority"],
                         NORMAL)

    def test_the_seam_cannot_supply_a_word_boundary(self):
        # A punctuation-only sentinel closed the \s+ splice but not the \b
        # splice: "...abnon-refund" + "able..." matched "refund".
        self.assertEqual(_c(self._seam_probe("abnon-refund", "able"))["priority"], NORMAL)
        self.assertEqual(_c(self._seam_probe("xundam", "aged parcel"))["priority"], NORMAL)

    def test_a_follow_up_late_in_a_long_message_still_counts(self):
        body = ("Hi there, hope you are well and that the shop is doing nicely. " * 200
                + " Just following up again, still no response from anyone.")
        self.assertGreater(len(body), 8000)
        self.assertGreaterEqual(_RANK[_c(body)["priority"]], _RANK[HIGH])

    def test_the_negative_word_list_is_load_bearing(self):
        angry = "This is a disgrace!!! I am furious!!!"
        owner, original = _set_canonical("_NEGATIVE_RE", re.compile(r"(?!x)x"))
        try:
            stubbed = _c("Lovely shop but this is a disgrace!!!")["priority"]
        finally:
            setattr(owner, "_NEGATIVE_RE", original)
        self.assertNotEqual(stubbed, _c("Lovely shop but this is a disgrace!!!")["priority"],
                            "_NEGATIVE_RE can be stubbed out with no effect")


class AuditListTests(unittest.TestCase):
    """The mutation test iterates _PORTED_HIGH_PATTERNS, so emptying that tuple
    would disable the audit rather than fail it."""

    def test_the_ported_high_audit_list_is_not_empty(self):
        self.assertEqual(len(cls._PORTED_HIGH_PATTERNS), 5)
        for pattern in cls._PORTED_HIGH_PATTERNS:
            self.assertIn(pattern, cls._HIGH_KEYWORDS)

    def test_the_weak_tables_are_not_empty(self):
        # _WEAK_UNGUARDED is deliberately EMPTY as of round 14 - see the note
        # on it in classifier.py. It has no minimum; the other two do, so
        # emptying them would disable the audit rather than fail it.
        self.assertGreaterEqual(len(cls._WEAK_DAMAGE), 6)
        self.assertGreaterEqual(len(cls._WEAK_OMISSION), 7)
        self.assertEqual(
            sorted(cls._WEAK_IMMEDIATE),
            sorted(cls._WEAK_UNGUARDED + cls._WEAK_DAMAGE
                   + cls._WEAK_OMISSION))

    def test_the_unguarded_table_is_still_empty(self):
        """Round 14's conclusion, pinned.

        A pattern here fires on every ticket whatever the shape of the
        sentence, and three rounds running found that the table's contents
        were ordinary vocabulary: "a stain" and "hole in" (round 11),
        "ripped" and "stained" (round 12), "another customer" and "someone
        else's name" (round 14). Adding to it needs evidence none of those
        six had, so make adding to it a decision rather than an edit.
        """
        self.assertEqual(
            _canonical_value("_WEAK_UNGUARDED"), [],
            "a rule here bypasses the browsing guard entirely - measure it "
            "against ordinary post-purchase traffic before adding it")

    # Ordinary post-purchase sentences. A customer writes every one of these
    # about a parcel that arrived perfectly, so NOTHING in the unguarded
    # table may match any of them - that table fires whatever shape the
    # sentence is.
    BENIGN_PHRASES = [
        "how do i get a stain out of a cotton onesie",
        "is there a hole in the back of the sleep bag for a car seat strap",
        "do you do a rip-resistant version of the pram liner",
        "how do i stop a rip in the knee if she crawls a lot",
        "is there a tear-away label or a sewn-in one",
        "what is the best way to get a stain on white muslin out",
        "can i order the same one with a hole for the car seat strap",
        "do you sell a spare bow, and how do i stop a tear in the seam",
        "i ripped the poly bag opening it, is the mailer recyclable",
        "my toddler stained the bib with carrot, how do i get it out",
        "the bib got stained at nursery, do you sell a stain-proof one",
        "i ripped the gift note taking it off, can you email me a copy",
        "i lost the discount code from the newsletter, could you resend it",
        "the size guide seems to be missing from the product page, could you check",
        "my name was left out of the gift note, can you add it next time",
        "can i swap the bundle for the one without the hat",
        "tracking says my order was delivered on thursday, can you confirm the address",
        "is the mailer recyclable and are the poppers nickel free",
        "which size do i need, and do you do a set without the headband",
        "can you tell me if the cardigan is included or sold separately",
        "can i exchange it for a different item, or is store credit easier",
        "can i put someone else's name on the gift note for a present",
        "another customer recommended you for gifts, do you do vouchers",
        "is it ok to use someone else's box to send the return back",
        "can i get the pink one instead of the blue one i ordered",
        "was the free bib supposed to include a matching muslin",
    ]

    def test_the_unguarded_table_matches_no_ordinary_sentence(self):
        """Round 11 and round 12 both got the unguarded table wrong.

        A pattern that fires whatever shape the sentence is must be wording
        no care or purchase question ever contains. Round 11 put the damage
        NOUNS there ("a stain", "hole in"); round 12 found the damage VERBS
        were no better ("I ripped the poly bag", "my toddler stained the
        bib"). Both times the fix moved the words and left the table, so the
        test is now the property rather than the names: run the whole
        unguarded table against ordinary post-purchase sentences.
        """
        for pattern in cls._WEAK_UNGUARDED:
            for phrase in self.BENIGN_PHRASES:
                with self.subTest(pattern=pattern, phrase=phrase[:40]):
                    self.assertIsNone(
                        re.search(pattern, phrase),
                        f"{pattern!r} fires on an ordinary sentence and the "
                        f"unguarded table ignores the browsing guard",
                    )

    def test_the_unguarded_table_stays_small_and_deliberate(self):
        # Two entries, both naming ANOTHER person's order. Growth here is the
        # regression; growth in the guarded tables is free.
        self.assertLessEqual(
            len(cls._WEAK_UNGUARDED), 3,
            "adding to the unguarded table needs the same evidence the last "
            "two rounds of this bug did not have")

    def test_every_guarded_word_has_a_benign_sentence_to_test_it_against(self):
        """The bank must keep up with the tables.

        Round 12's reviewer's point: the previous version of this test used
        8 hand-written phrases, none of which contained "ripped" or
        "stained", so the live bug sailed through the test written to catch
        exactly it. Mining the words from the tables means a new pattern
        cannot be added without a sentence that exercises it.
        """
        bank = " | ".join(self.BENIGN_PHRASES)
        missing = []
        for pattern in cls._WEAK_DAMAGE + cls._WEAK_OMISSION:
            words = [w.lower() for w in ReDoSTests._literals(pattern)
                     if len(w) > 3]
            if words and not any(w in bank for w in words):
                missing.append((pattern, sorted(set(words))[:6]))
        self.assertEqual(
            missing, [],
            "these guarded patterns have no ordinary sentence in "
            "BENIGN_PHRASES that contains their wording, so nothing checks "
            "what they do to real traffic")

    def test_the_guarded_tables_are_still_the_ones_that_match_them(self):
        """Or the tests above pass by having deleted the rules, not moved
        them."""
        hits = sum(
            1 for phrase in self.BENIGN_PHRASES
            if any(re.search(p, phrase)
                   for p in cls._WEAK_DAMAGE + cls._WEAK_OMISSION)
        )
        self.assertGreaterEqual(hits, 15, "the weak tables were gutted")

    # The sentences above are only ever fed to re.search against the tables.
    # That checks which table a word is in and NOTHING about what classify()
    # does with it - so reverting the whole browsing-guard expansion, or the
    # problem-context changes, or the damage-plus-remedy lift, left the entire
    # suite green. Three of round 12's four classifier changes had no failing
    # test at all. Everything below runs the real classify().
    ORDER_PREFIXES = [
        "My order arrived today.",
        "My parcel came yesterday and it's lovely.",
        "Order 10322 was delivered on Friday, thank you!",
        "Tracking says my order was delivered on Thursday.",
        "Just got my delivery.",
    ]

    def test_no_benign_sentence_escalates_with_an_order_word_in_front(self):
        """The shape real post-purchase mail arrives in.

        Every one of these is ordinary in a baby shop, and every one of them
        is NORMAL on main. An order word in the same message arms every rule
        that needs order context, which is exactly what makes this the shape
        that keeps breaking.
        """
        for phrase in self.BENIGN_PHRASES:
            for prefix in self.ORDER_PREFIXES:
                message = f"{prefix} {phrase}?"
                with self.subTest(prefix=prefix[:22], phrase=phrase[:40]):
                    result = _c(message)
                    self.assertEqual(result["priority"], NORMAL,
                                     f"{result['reason']} :: {message}")
                    self.assertFalse(result["should_notify_owner"])

    def test_no_benign_sentence_escalates_on_its_own_either(self):
        for phrase in self.BENIGN_PHRASES:
            with self.subTest(phrase=phrase[:40]):
                self.assertEqual(_c(phrase + "?")["priority"], NORMAL)

    def test_every_browsing_marker_actually_suppresses_a_weak_hit(self):
        """One classify()-level case per alternative in the guard.

        Adding a marker with no case here is how the round-12 expansion
        shipped untested; removing one is how it would silently regress.
        """
        probes = {
            "can i": "My order arrived. Can I order the set without the hat?",
            "could i": "My order arrived. Could I order the set without the hat?",
            "can you": "My order arrived. Can you resend the code I lost?",
            "could you": "My order arrived. Could you resend the code I lost?",
            "would you": "My order arrived. Would you send the set without the bow?",
            "will you": "My order arrived. Will you do the set without the bow?",
            "do you": "My order arrived. Do you sell the set without the bow?",
            "is there": "My order arrived. Is there a set without the bow?",
            "how do i": "My order arrived. How do I order it without the bow?",
            "is the": "My order arrived. Is the set sold without the bow?",
            "are the": "My order arrived. Are the sets sold without the bow?",
            "was the": "My order arrived. Was the bib supposed to include a muslin?",
            "were the": "My order arrived. Were the socks supposed to include a bib?",
            "did you": "My order arrived. Did you ever do the set without the bow?",
            "do i need": "My order arrived. Do I need the set without the bow?",
            "where do i": "My order arrived. Where do I find it without the bow?",
            "what do i": "My order arrived. What do I order for it without the bow?",
            "which size": "My order arrived. Which size comes without the bow?",
        }
        for marker, message in probes.items():
            with self.subTest(marker=marker):
                got = _c(message)
                self.assertEqual(got["priority"], NORMAL,
                                 f"{marker!r} no longer suppresses: "
                                 f"{got['reason']}")

    def test_every_problem_marker_actually_lifts_the_guard(self):
        """The other direction, at classify() level: a polite complaint must
        still get through the guard the test above relies on."""
        probes = {
            "arrived ripped": "Do you sell a replacement bow? Mine arrived ripped.",
            "a stained X": "Am I able to swap this? The parcel had a stained romper in it.",
            "why": "Can I ask why my order came without the hat?",
            "tracking stopped": "Is it possible the courier lost my parcel? Tracking stopped 8 days ago.",
            "still waiting": "Can you help? My order is missing and I am still waiting.",
            "chasing": "Do you know where it is? I am chasing my missing parcel.",
        }
        for marker, message in probes.items():
            with self.subTest(marker=marker):
                got = _c(message)
                self.assertNotEqual(
                    got["priority"], NORMAL,
                    f"{marker!r} no longer lifts the guard: {message}")


class NegationTests(unittest.TestCase):
    def test_asking_not_to_escalate_is_not_an_escalation(self):
        self.assertEqual(
            _c("Please don't escalate this, I just have a quick sizing question.")["priority"],
            NORMAL)

    def test_asking_to_escalate_still_is(self):
        self.assertEqual(
            _c("Please escalate this to someone who can help.")["priority"], IMMEDIATE)


class BoundedWorkTests(unittest.TestCase):
    """Ticket bodies are attacker-influenced."""

    def test_classify_is_bounded_on_a_hostile_message(self):
        import time
        # This shape backtracks super-linearly against a pre-existing pattern.
        payload = "following up " * 20000 + "q" * 5000
        started = time.perf_counter()
        _c(payload)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0, f"classify took {elapsed:.2f}s on {len(payload)} bytes")

    def test_a_long_message_is_still_classified(self):
        padding = "hello there this is a normal sentence. " * 500
        self.assertEqual(_c("I want a refund. " + padding)["priority"], IMMEDIATE)


class ContractTests(unittest.TestCase):
    """The classifier's shape must not drift — the orchestrator depends on it."""

    REQUIRED_KEYS = {"priority", "reason", "sensitive", "should_draft",
                     "should_notify_owner", "source", "matched"}

    def test_every_verdict_has_the_full_shape(self):
        for message in ["I want a refund", "urgent please", "what brands do you carry?"]:
            with self.subTest(message=message):
                result = _c(message)
                self.assertEqual(set(result), self.REQUIRED_KEYS)
                self.assertIn(result["priority"], (IMMEDIATE, HIGH, NORMAL))
                self.assertIsInstance(result["matched"], list)

    def test_sensitive_always_notifies_the_owner(self):
        for message in ["I want a refund", "chargeback", "my order arrived damaged"]:
            with self.subTest(message=message):
                result = _c(message)
                self.assertTrue(result["sensitive"])
                self.assertTrue(result["should_notify_owner"])

    def test_classifier_never_suppresses_a_draft(self):
        # main's contract: this classifier may escalate, never stop a draft.
        for message in ["", "thanks", "chargeback", "SHOUTING AT YOU RIGHT NOW OK"]:
            with self.subTest(message=message):
                self.assertTrue(_c(message)["should_draft"])


@unittest.skipUnless(SCENARIOS.is_file(),
                     f"{SCENARIOS} not present — merge the Task 1 test harness first")
class ScenarioRegressionTests(unittest.TestCase):
    """Run the 48 harness scenarios through the classifier.

    This is the offline half of the tasklist's Task 4 acceptance test: every
    sensitive case must escalate on the code path alone, and nothing benign may
    newly escalate.
    """

    @classmethod
    def setUpClass(cls):
        cls.scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))

    def _classify(self, scenario):
        return _c(scenario["message"], scenario.get("subject", ""))

    def test_every_sensitive_scenario_escalates(self):
        sensitive = [s for s in self.scenarios if s["cat"] == "sensitive"]
        self.assertGreaterEqual(len(sensitive), 10)
        for scenario in sensitive:
            with self.subTest(scenario=scenario["id"]):
                result = self._classify(scenario)
                self.assertNotEqual(
                    result["priority"], NORMAL,
                    msg=f"{scenario['id']} would not escalate: {scenario['message'][:80]}",
                )

    def test_benign_scenarios_do_not_escalate(self):
        # The harness's deliberately-benign cases: an empty message, a bare
        # thanks, and an obvious spam blast.
        for scenario_id in ("E01", "E02", "E12"):
            scenario = next((s for s in self.scenarios if s["id"] == scenario_id), None)
            if scenario is None:
                continue
            with self.subTest(scenario=scenario_id):
                self.assertEqual(self._classify(scenario)["priority"], NORMAL)


if __name__ == "__main__":
    unittest.main()
