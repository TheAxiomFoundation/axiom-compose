"""Deterministic RuleSpec composition."""

from .core import (
    ComposeError,
    ComposeWarning,
    CorpusIndex,
    CorpusState,
    RuleSpecModule,
    RunnableProgram,
    compose,
    load_corpus_from_roots,
    with_corpus_index,
)
from .spec import ProgramSpec, load_spec

__all__ = [
    "ComposeError",
    "ComposeWarning",
    "CorpusState",
    "CorpusIndex",
    "ProgramSpec",
    "RuleSpecModule",
    "RunnableProgram",
    "compose",
    "load_corpus_from_roots",
    "load_spec",
    "with_corpus_index",
]
