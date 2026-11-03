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

The pinned tree comes from the checkout at ``AXIOM_RULESPEC_US_ROOT`` when it
has the commit. When it lacks it (CI checks out depth 1), the commit is
fetched at depth 1 from the checkout's origin URL into a throwaway bare
repository, never into the checkout (``tests/pinned_tree.py``). Without the
env var, or if the fetch fails, the pinned tests skip.
"""

import os
import subprocess
from pathlib import Path

import pytest
from pinned_tree import (
    PinUnavailable,
    extract_pinned_tree,
    git,
    repository_state,
)
from real_corpus import corpus

from axiom_compose import compose, load_spec

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


@pytest.fixture(scope="module")
def pinned_root(tmp_path_factory) -> Path:
    """rulespec-us at the pinned commit, extracted outside the checkout."""

    checkout = _checkout()
    try:
        return extract_pinned_tree(
            checkout, PINNED_RULESPEC_US_SHA, tmp_path_factory.mktemp("pinned")
        )
    except PinUnavailable as error:
        pytest.skip(f"cannot get rulespec-us {PINNED_RULESPEC_US_SHA}: {error}")


def _compose_text(spec_path: Path, root: Path) -> str:
    # Both runs carry the pinned label, so only content compose reads differs.
    labelled = corpus(root, f"rulespec-us@{PINNED_RULESPEC_US_SHA}")
    return compose(load_spec(spec_path), labelled).text()


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


# --- the pinned fixture never writes to the checkout ----------------------------


def _run(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        list(args), cwd=cwd, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    _run("git", "add", name, cwd=repo)
    _run(
        "git",
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        name,
        cwd=repo,
    )
    return _run("git", "rev-parse", "HEAD", cwd=repo)


@pytest.mark.parametrize("pin_in_checkout", [False, True])
def test_pinned_tree_leaves_a_full_checkout_unchanged(tmp_path, pin_in_checkout):
    # A full (non-shallow) clone that lacks the pin is the case that a
    # `fetch --depth 1` into the checkout turned shallow. Hermetic: the
    # "origin" is a local repository served over file://.
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _run("git", "init", "--quiet", cwd=upstream)
    _run("git", "config", "uploadpack.allowAnySHA1InWant", "true", cwd=upstream)
    _commit(upstream, "a.yaml", "first\n")
    _commit(upstream, "b.yaml", "second\n")
    checkout = tmp_path / "checkout"
    _run("git", "clone", "--quiet", upstream.as_uri(), str(checkout), cwd=tmp_path)
    pin = _commit(upstream, "c.yaml", "pinned\n")
    if pin_in_checkout:
        _run("git", "fetch", "--quiet", "origin", cwd=checkout)
    before = repository_state(checkout)
    assert before["is_shallow"] == "false" and before["shallow_file"] is None
    assert (git(checkout, "cat-file", "-e", pin).returncode == 0) is pin_in_checkout
    history = _run("git", "rev-list", "--count", "--all", cwd=checkout)

    root = extract_pinned_tree(checkout, pin, tmp_path / "work")

    assert sorted(p.name for p in root.iterdir()) == ["a.yaml", "b.yaml", "c.yaml"]
    assert (root / "c.yaml").read_text() == "pinned\n"
    assert repository_state(checkout) == before
    assert _run("git", "rev-list", "--count", "--all", cwd=checkout) == history
    assert (git(checkout, "cat-file", "-e", pin).returncode == 0) is pin_in_checkout


def test_pinned_tree_reports_an_unfetchable_pin(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _run("git", "init", "--quiet", cwd=upstream)
    _commit(upstream, "a.yaml", "first\n")
    checkout = tmp_path / "checkout"
    _run("git", "clone", "--quiet", upstream.as_uri(), str(checkout), cwd=tmp_path)
    before = repository_state(checkout)

    with pytest.raises(PinUnavailable):
        extract_pinned_tree(checkout, "0" * 40, tmp_path / "work")

    assert repository_state(checkout) == before
