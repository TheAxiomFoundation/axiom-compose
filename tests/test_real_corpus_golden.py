"""Real-corpus golden tests for deterministic composed output (#4).

Each case composes a real program spec against rulespec-us at a pinned
commit and compares the emitted YAML byte for byte with a golden string kept in
this file. The goldens were produced by running axiom-compose against that commit; they
are not hand-written.

Two checks per case:

- at the pinned commit, so a composer change that alters output fails;
- at the checkout CI provides (rulespec-us main), so an upstream content
  change that alters output fails too. When that fires on purpose, move
  ``PINNED_RULESPEC_US_SHA`` to the new commit and regenerate the goldens
  (``AXIOM_COMPOSE_REGENERATE_GOLDENS=1 pytest -s`` prints them) in the same
  change.

The pinned tree comes from the checkout at ``AXIOM_RULESPEC_US_ROOT``; when
that checkout lacks the commit (CI checks out depth 1), the test fetches just
that commit from the checkout's origin. Without the env var, or if the fetch
fails, the tests skip.
"""

import functools
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from axiom_compose import compose, load_corpus_from_roots, load_spec

ROOT = Path(__file__).parent

PINNED_RULESPEC_US_SHA = "fe62a527af2315170d653c413345e27834a5d221"

CASES = {
    "eitc": ROOT / "fixtures" / "eitc-spec.yaml",
    "ctc": ROOT / "fixtures" / "ctc-spec.yaml",
}

# Produced by composing each spec against rulespec-us at the pinned SHA
# (`AXIOM_COMPOSE_REGENERATE_GOLDENS=1` prints the current text instead of
# comparing). Neither spec has transformations, so the emitted program is
# the module header plus the dependency closure in emission order.
GOLDENS = {
    "eitc": (
        "format: rulespec/v1\n"
        "module:\n"
        "  kind: composition\n"
        "  summary: 'Deterministic composition for us/fiit/eitc at 2026. Outputs: eitc, eitc_allowed, eitc_before_eligibility. Corpus: rulespec-us@fe62a527af2315170d653c413345e27834a5d221.'\n"
        "imports:\n"
        "- us:statutes/26/32\n"
        "- us:statutes/26/32/c/2\n"
        "- us:statutes/26/112\n"
        "- us:statutes/26/152/c\n"
        "- us:statutes/26/7703\n"
        "- us:statutes/26/151\n"
        "- us:statutes/26/911/a\n"
        "- us:statutes/26/931\n"
        "- us:statutes/26/933\n"
        "- us:policies/irs/rev-proc-2025-32/earned-income-credit\n"
    ),
    "ctc": (
        "format: rulespec/v1\n"
        "module:\n"
        "  kind: composition\n"
        "  summary: 'Deterministic composition for us/fiit/ctc at 2026. Outputs: ctc_after_advance_payments, refundable_ctc, non_refundable_ctc. Corpus: rulespec-us@fe62a527af2315170d653c413345e27834a5d221.'\n"
        "imports:\n"
        "- us:statutes/26/24\n"
        "- us:statutes/26/24/h\n"
        "- us:statutes/26/24/d\n"
        "- us:statutes/26/32/c/2\n"
        "- us:statutes/26/112\n"
        "- us:statutes/26/32\n"
        "- us:statutes/26/152/c\n"
        "- us:statutes/26/7703\n"
        "- us:statutes/26/151\n"
        "- us:statutes/26/911/a\n"
        "- us:statutes/26/931\n"
        "- us:statutes/26/933\n"
        "- us:policies/irs/rev-proc-2025-32/earned-income-credit\n"
        "- us:policies/irs/rev-proc-2025-32/child-tax-credit\n"
    ),
}


def _checkout() -> Path:
    raw = os.environ.get("AXIOM_RULESPEC_US_ROOT")
    if not raw:
        pytest.skip("AXIOM_RULESPEC_US_ROOT is not set")
    path = Path(raw).resolve()
    if not path.exists():
        raise AssertionError(f"AXIOM_RULESPEC_US_ROOT points to missing path: {path}")
    return path


def _git(checkout: Path, *args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(checkout), *args],
        check=False,
        capture_output=True,
        **kwargs,
    )


@pytest.fixture(scope="module")
def pinned_root(tmp_path_factory) -> Path:
    """rulespec-us at the pinned commit, extracted outside the checkout."""

    checkout = _checkout()
    if _git(
        checkout, "cat-file", "-e", f"{PINNED_RULESPEC_US_SHA}^{{commit}}"
    ).returncode:
        fetched = _git(
            checkout,
            "fetch",
            "--quiet",
            "--depth",
            "1",
            "origin",
            PINNED_RULESPEC_US_SHA,
        )
        if fetched.returncode:
            pytest.skip(
                f"cannot fetch rulespec-us {PINNED_RULESPEC_US_SHA}: "
                + fetched.stderr.decode(errors="replace")[-500:]
            )
    # The directory name matters: the loader reads the country from
    # `rulespec-<country>`.
    root = tmp_path_factory.mktemp("pinned") / "rulespec-us"
    root.mkdir()
    archive = root.parent / "tree.tar"
    with archive.open("wb") as handle:
        archived = subprocess.run(
            ["git", "-C", str(checkout), "archive", PINNED_RULESPEC_US_SHA],
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert archived.returncode == 0, archived.stderr.decode(errors="replace")
    with tarfile.open(archive) as tar:
        tar.extractall(root, filter="data")
    archive.unlink()
    return root


@functools.cache
def _corpus(root: Path):
    # Loading rulespec-us takes about a minute; load each root once.
    return load_corpus_from_roots(
        [root], corpus_sha=f"rulespec-us@{PINNED_RULESPEC_US_SHA}"
    )


def _compose_text(spec_path: Path, root: Path) -> str:
    return compose(load_spec(spec_path), _corpus(root)).text()


def _check(name: str, text: str, message: str) -> None:
    if os.environ.get("AXIOM_COMPOSE_REGENERATE_GOLDENS") == "1":
        print(f"--- {name}\n{text}")
        return
    assert text == GOLDENS[name], message


@pytest.mark.parametrize("name", sorted(CASES))
def test_pinned_corpus_composes_to_golden(name, pinned_root):
    _check(
        name,
        _compose_text(CASES[name], pinned_root),
        f"composed {name} at pinned rulespec-us {PINNED_RULESPEC_US_SHA} no "
        "longer matches its golden: axiom-compose output changed",
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_current_corpus_still_composes_to_golden(name):
    # The summary line names the pinned SHA in both runs, so only content
    # that compose reads can differ.
    _check(
        name,
        _compose_text(CASES[name], _checkout()),
        f"composed {name} against the current rulespec-us checkout differs "
        f"from the golden pinned at {PINNED_RULESPEC_US_SHA}: upstream "
        "content changed the composition. Re-pin the SHA and regenerate the "
        "goldens if the change is intended.",
    )
