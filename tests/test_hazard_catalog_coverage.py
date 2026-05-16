"""T-519 — §20 hazard-catalog test-coverage audit (F5 E4 reproducible guard).

E4 (BRIEF §19): "All hazards in §20 have an associated test that passes."
§20 preamble: "Every hazard has an associated test (or set of tests) that
fails if the hazard recurs."

The shipped mechanism is the **test-NAME-pin** convention: each `### H-NNN`
entry's `**Test.**` block cites its controlling test(s) by name inline in
backticks. The task-def's `pytest -m hazard` / `@pytest.mark.hazard`
mechanism is a stale pre-ADR-0011 artefact — code-verified non-existent
(0 tree-wide marker usages; pyproject `--strict-markers` + no `markers=[]`
block would reject it). This module is the reverse-map audit + the
permanent CI guard: a future §20 entry citing a non-existent test fails
here (not silently rotting a hand-authored doc).

Catalog scope is H-001..H-036 (the task-def's "H-001..H-026" is doubly
-stale: H-027/028/029 allocated 2026-05-15/16 at T-525a1/T-534b2/T-535;
H-030..H-036 via operator audit 2026-05-08/09 — all postdate the task-def).
The contiguity check is on the SORTED INTEGER SET, NOT file-appearance
order: §20 entries appear out of file order (`... H-031 H-034 H-035 H-036
H-032 H-033` — H-032/H-033 are physically after H-036 by deliberate
audit-cluster grouping, each with a `numbering note`); a complete catalog
with out-of-file-order entries MUST pass.

Citation parser — a backtick span on/under the `**Test.**` line is a
citation iff it is one of exactly 3 shapes (everything else, e.g.
`bus.close`, `call_order == [...]`, `Decimal("0.0015") // Decimal(...)`,
`QtyValidationError`, is a code-ref and ignored):

* bare function name: `` `test_foo` ``
* path with node: `` `pkg/tests/test_x.py::test_foo` `` → function `test_foo`
* file-level: `` `pkg/tests/test_x.py` `` (no `::`) → assert the file has
  >=1 collected test

Multiple citations on one line are `+`-joined; the `**Test.**` "block" runs
from the `**Test.**` marker to the next `### H-` (citations may sit on a
later `Test sites:` line, e.g. H-035 — so scan the whole entry tail).

The collected-test set is obtained via a subprocess `pytest
--collect-only -q` (the deterministic-tool-subprocess precedent of
`tests/integration/migrations/test_0021_migration.py`'s alembic shell-out)
— collect-only NEVER runs tests (no recursion / self-timeout).

Gate-4: OUT OF SCOPE (markdown-meta-task, T-523/T-533a class — zero
Decimal arithmetic; the parser/asserts are pure string/set ops). §N4 is
satisfied by this module self-testing its own parser on hand-authored
fixtures (the 3 shapes, code-ref ignore, out-of-file-order-complete,
synthetic gap, unresolved citation).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BRIEF = _REPO_ROOT / "docs" / "CLAUDE_CODE_BRIEF.md"

_SECTION_20_START = "## 20. Known Hazards Catalog"
_SECTION_END_RE = re.compile(r"^## 21\. ", re.MULTILINE)
_ENTRY_RE = re.compile(r"^### H-(\d{3}) ", re.MULTILINE)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_BARE_FN_RE = re.compile(r"^test_[A-Za-z0-9_]+$")
_PATH_RE = re.compile(r"^[\w./\-]+\.py(::[\w]+(::test_[A-Za-z0-9_]+)?|::test_[A-Za-z0-9_]+)?$")

# Operator-acknowledged DEFERRED carve-outs (T-519 audit 2026-05-16): a §20
# hazard whose control is genuinely not yet implemented, deferred to a T-F5+
# backlog ticket with explicit operator sign-off (residual-risk accepted for
# the paper-feature-complete MVP). H-005 (opposite_side_open scoring rule —
# not in packages/scoring/conditions/, no execution-side equivalent). The set
# is the tight whitelist: a NEW deferral not here, OR a known-deferred entry
# that loses its DEFERRED marker, is a finding (cannot silently widen/regress).
_KNOWN_DEFERRED: frozenset[int] = frozenset({5})


def _section_20_text(brief: str) -> str:
    """Slice the §20 block: from the `## 20.` heading to the `## 21.` heading."""
    start = brief.index(_SECTION_20_START)
    tail = brief[start:]
    m = _SECTION_END_RE.search(tail)
    return tail[: m.start()] if m else tail


def _normalize_citation(span: str) -> tuple[str, str] | None:
    """Classify a backtick span. Return (kind, value) or None if not a citation.

    kind="func" → value is a test function name (asserted in collected funcs).
    kind="file" → value is a test file path (asserted has >=1 collected test).
    """
    s = span.strip()
    if "::" in s:
        node = s.split("::")[-1].split("[")[0].strip()
        if _BARE_FN_RE.match(node) and _PATH_RE.match(s):
            return ("func", node)
        return None
    if _BARE_FN_RE.match(s):
        return ("func", s)
    if s.endswith(".py") and _PATH_RE.match(s):
        return ("file", s)
    return None


def _extract_citations(section_text: str) -> dict[int, list[tuple[str, str]]]:
    """Map H-NNN int → list of (kind, value) citations from its `**Test.**` tail.

    Entry tail = from the entry's `**Test.**` marker to the next `### H-`
    (or section end). Entries with NO `**Test.**` marker, or whose tail
    yields zero citations, map to an empty list (an audit finding).
    """
    bounds = [(int(m.group(1)), m.start()) for m in _ENTRY_RE.finditer(section_text)]
    out: dict[int, list[tuple[str, str]]] = {}
    for idx, (hnum, start) in enumerate(bounds):
        end = bounds[idx + 1][1] if idx + 1 < len(bounds) else len(section_text)
        entry = section_text[start:end]
        cites: list[tuple[str, str]] = []
        test_marker = entry.find("**Test.**")
        if test_marker != -1:
            for span in _BACKTICK_RE.findall(entry[test_marker:]):
                c = _normalize_citation(span)
                if c is not None and c not in cites:
                    cites.append(c)
        out[hnum] = cites
    return out


def _deferred_hazards(section_text: str) -> set[int]:
    """H-NNN whose `**Test.**` block is an explicit DEFERRED carve-out — the
    literal `DEFERRED` token AND a `T-F5+` backlog reference present."""
    bounds = [(int(m.group(1)), m.start()) for m in _ENTRY_RE.finditer(section_text)]
    out: set[int] = set()
    for idx, (hnum, start) in enumerate(bounds):
        end = bounds[idx + 1][1] if idx + 1 < len(bounds) else len(section_text)
        entry = section_text[start:end]
        tm = entry.find("**Test.**")
        block = entry[tm:] if tm != -1 else ""
        if "DEFERRED" in block and "T-F5+" in block:
            out.add(hnum)
    return out


@pytest.fixture(scope="session")
def collected_tests() -> tuple[frozenset[str], frozenset[str]]:
    """(collected test func-names, collected test file-paths) via a
    subprocess `pytest --collect-only -q` (deterministic; collect-only
    never RUNS tests → no recursion / self-timeout)."""
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        timeout=600,
    )
    funcs: set[str] = set()
    files: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if ".py::" not in line:
            continue
        file_part = line.split("::", 1)[0]
        func_part = line.split("::")[-1].split("[")[0]
        files.add(file_part)
        funcs.add(func_part)
    return frozenset(funcs), frozenset(files)


# --- Live §20 audit (the E4 guard) ----------------------------------------


def test_section20_catalog_is_contiguous_h001_to_max() -> None:
    """Parsed H-integers, SORTED as a SET, == range(1, max+1). NOT
    file-appearance order (H-032/H-033 are physically after H-036)."""
    section = _section_20_text(_BRIEF.read_text(encoding="utf-8"))
    nums = sorted({int(m.group(1)) for m in _ENTRY_RE.finditer(section)})
    assert nums, "no §20 hazard entries parsed"
    expected = list(range(1, nums[-1] + 1))
    missing = sorted(set(expected) - set(nums))
    assert nums == expected, (
        f"§20 catalog not contiguous H-001..H-{nums[-1]:03d}; missing {missing}"
    )


def test_every_hazard_has_a_resolvable_test_citation(
    collected_tests: tuple[frozenset[str], frozenset[str]],
) -> None:
    """Every §20 H-NNN cites >=1 test; every citation resolves to a
    pytest-collected function (or file with >=1 collected test). The
    permanent E4 guard: a stale/missing citation fails CI here."""
    funcs, files = collected_tests
    section = _section_20_text(_BRIEF.read_text(encoding="utf-8"))
    citations = _extract_citations(section)
    deferred = _deferred_hazards(section)
    findings: list[str] = []
    # Whitelist integrity: every known-DEFERRED hazard MUST still be
    # DEFERRED-marked in §20 — a re-implemented / regressed entry must NOT be
    # silently covered by the stale whitelist.
    for hnum in sorted(_KNOWN_DEFERRED):
        if hnum not in deferred:
            findings.append(
                f"H-{hnum:03d}: in _KNOWN_DEFERRED but no longer DEFERRED-marked in "
                f"§20 — cite a real test or update the whitelist (E4 carve-out review)"
            )
    for hnum in sorted(citations):
        cites = citations[hnum]
        if not cites:
            if hnum in deferred:
                if hnum in _KNOWN_DEFERRED:
                    continue  # operator-acknowledged carve-out (E4 35/36)
                findings.append(
                    f"H-{hnum:03d}: DEFERRED but NOT in _KNOWN_DEFERRED — operator "
                    f"must acknowledge the carve-out (T-519 / E4) before CI can pass"
                )
            else:
                findings.append(f"H-{hnum:03d}: NO test citation in **Test.** block")
            continue
        for kind, value in cites:
            ok = value in funcs if kind == "func" else value in files
            if not ok:
                findings.append(f"H-{hnum:03d}: {kind} `{value}` unresolved (not collected)")
    assert not findings, "§20 hazard test-coverage findings:\n" + "\n".join(findings)


# --- Parser self-tests (§N4 — hand-authored fixtures, no subprocess) -------

_FIX_THREE_SHAPES = """## 20. Known Hazards Catalog

### H-001 — bare
**Test.** `test_bare_name` verifies the thing.

### H-002 — path with node
**Test.** `pkg/tests/test_mod.py::test_with_node` (mock-based).

### H-003 — file level
**Test.** `pkg/tests/test_quantize.py` 4 unit tests with Decimal fixtures.

## 21. Glossary
"""

_FIX_CODE_REFS = """## 20. Known Hazards Catalog

### H-001 — code refs ignored
**Test.** `test_real_one` asserts `call_order == ["a", "b"]` and
`Decimal("0.0015") // Decimal("0.001")` and `bus.close` / `pool.close`.

## 21. Glossary
"""

_FIX_OUT_OF_ORDER = """## 20. Known Hazards Catalog

### H-001 — a
**Test.** `test_a`.

### H-003 — c (out of file order — physically before H-002)
**Test.** `test_c`.

### H-002 — b
**Test.** `test_b`.

## 21. Glossary
"""

_FIX_GAP = """## 20. Known Hazards Catalog

### H-001 — a
**Test.** `test_a`.

### H-003 — c (H-002 missing → gap)
**Test.** `test_c`.

## 21. Glossary
"""


def test_parser_extracts_exactly_the_three_citation_shapes() -> None:
    cites = _extract_citations(_section_20_text(_FIX_THREE_SHAPES))
    assert cites[1] == [("func", "test_bare_name")]
    assert cites[2] == [("func", "test_with_node")]
    assert cites[3] == [("file", "pkg/tests/test_quantize.py")]


def test_parser_ignores_code_ref_backticks() -> None:
    cites = _extract_citations(_section_20_text(_FIX_CODE_REFS))
    # Only the real test name survives; call_order/Decimal/bus.close dropped.
    assert cites[1] == [("func", "test_real_one")]


def test_contiguity_passes_on_out_of_file_order_complete() -> None:
    section = _section_20_text(_FIX_OUT_OF_ORDER)
    nums = sorted({int(m.group(1)) for m in _ENTRY_RE.finditer(section)})
    assert nums == [1, 2, 3]  # complete set despite H-003-before-H-002 in file


def test_contiguity_detects_synthetic_gap() -> None:
    section = _section_20_text(_FIX_GAP)
    nums = sorted({int(m.group(1)) for m in _ENTRY_RE.finditer(section)})
    expected = list(range(1, nums[-1] + 1))
    missing = sorted(set(expected) - set(nums))
    assert nums != expected
    assert missing == [2]  # H-002 correctly identified as the gap


def test_unresolved_citation_is_a_finding() -> None:
    cites = _extract_citations(_section_20_text(_FIX_THREE_SHAPES))
    funcs = frozenset({"test_bare_name"})  # test_with_node deliberately absent
    files = frozenset({"pkg/tests/test_quantize.py"})
    findings: list[str] = []
    for hnum, cs in cites.items():
        for kind, value in cs:
            ok = value in funcs if kind == "func" else value in files
            if not ok:
                findings.append(f"H-{hnum:03d}: {kind} `{value}` unresolved")
    assert findings == ["H-002: func `test_with_node` unresolved"]


_FIX_DEFERRED = """## 20. Known Hazards Catalog

### H-001 — normal
**Test.** `test_a`.

### H-005 — opposite-side (deferred)
**Test.** DEFERRED — no test (the scoring rule does not exist). Tracked by
the T-F5+ backlog ticket. Whitelisted as known-DEFERRED.

### H-006 — rogue deferral (NOT in _KNOWN_DEFERRED)
**Test.** DEFERRED — punted to a T-F5+ ticket without operator sign-off.

## 21. Glossary
"""


def test_deferred_carveout_detected_and_tightly_gated() -> None:
    section = _section_20_text(_FIX_DEFERRED)
    deferred = _deferred_hazards(section)
    assert deferred == {5, 6}  # both DEFERRED+T-F5+ entries detected
    cites = _extract_citations(section)
    assert cites[5] == []  # no backtick test citations (DEFERRED)
    assert cites[6] == []
    # The gating logic: H-005 ∈ _KNOWN_DEFERRED → accepted carve-out;
    # H-006 DEFERRED but ∉ _KNOWN_DEFERRED → finding (cannot silently widen).
    findings: list[str] = []
    for hnum in sorted(cites):
        if not cites[hnum]:
            if hnum in deferred and hnum not in _KNOWN_DEFERRED:
                findings.append(f"H-{hnum:03d}: DEFERRED not acknowledged")
            elif hnum not in deferred:
                findings.append(f"H-{hnum:03d}: no citation")
    assert findings == ["H-006: DEFERRED not acknowledged"]
    assert 5 in _KNOWN_DEFERRED
    assert 6 not in _KNOWN_DEFERRED
