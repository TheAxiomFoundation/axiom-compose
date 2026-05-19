"""Deterministic RuleSpec composition."""

from .core import (
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
