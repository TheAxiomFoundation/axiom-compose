"""One rulespec-us load per root, shared by the real-corpus test modules.

Loading rulespec-us takes about 80 seconds. ``tests/test_real_corpus_golden.py``
and ``tests/test_real_round_trip.py`` both compose against the
``AXIOM_RULESPEC_US_ROOT`` checkout, so they share this module's cache instead
of each loading the checkout again. ``corpus_sha`` only feeds the composed
module's summary line, so each caller relabels the shared state with its own.
"""

from __future__ import annotations

import dataclasses
import functools
from pathlib import Path

from axiom_compose import CorpusState, load_corpus_from_roots


@functools.cache
def _load(root: Path) -> CorpusState:
    return load_corpus_from_roots([root])


def corpus(root: Path, corpus_sha: str) -> CorpusState:
    return dataclasses.replace(_load(root.resolve()), corpus_sha=corpus_sha)
