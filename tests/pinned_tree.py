"""Extract a rulespec checkout's tree at a pinned commit, read-only.

The checkout a developer points ``AXIOM_RULESPEC_US_ROOT`` at must never be
written to. ``git fetch --depth 1`` into a full clone writes ``.git/shallow``
and grafts the fetched commit as a parentless root, which breaks later
merges there. ``git archive`` in a partial clone lazily fetches missing
blobs into its object store. So the tree is read from the checkout only
when the checkout has the commit and is not a partial clone; otherwise the
commit is fetched into a throwaway repository and archived from that.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tarfile
from pathlib import Path


class PinUnavailable(RuntimeError):
    """The pinned commit is neither in the checkout nor fetchable."""


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _has_commit(repo: Path, sha: str) -> bool:
    return git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def is_partial_clone(repo: Path) -> bool:
    """True when git may lazily fetch missing objects into ``repo``.

    Detects, through ``git config`` reads only:

    - ``extensions.partialClone`` set to any value;
    - any ``remote.<name>.promisor`` that git reads as true, including a
      bare key and nonzero numbers (``--type=bool`` normalises them);
    - any ``remote.<name>.partialclonefilter``.

    A ``promisor`` value git cannot read as a boolean also counts, so an
    unreadable config takes the scratch path rather than the checkout.
    """

    if git(repo, "config", "--get", "extensions.partialClone").stdout.strip():
        return True
    promisors = git(
        repo, "config", "--type=bool", "--get-regexp", r"^remote\..*\.promisor$"
    )
    if promisors.returncode not in (0, 1):
        return True
    if any(line.endswith(" true") for line in promisors.stdout.splitlines()):
        return True
    filters = git(repo, "config", "--get-regexp", r"^remote\..*\.partialclonefilter$")
    return filters.returncode == 0 and bool(filters.stdout.strip())


# URL forms git does not read as a local path: `scheme://...` and the
# scp-like `[user@]host:path` (a colon before any slash).
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://|^[^/]*:")


def fetch_source(checkout: Path, url: str) -> str:
    """The checkout's origin URL as the scratch repository must see it.

    A relative local path (``../up``) names a path relative to the
    checkout, but the fetch runs in the scratch repository, so it is
    resolved against the checkout first. URLs, scp-like forms and absolute
    paths pass through unchanged. So does anything starting with ``-``,
    which is not a path anyone means; ``--`` in the fetch refuses it.
    """

    if url.startswith("-") or _URL_RE.match(url) or os.path.isabs(url):
        return url
    return str((checkout / url).resolve())


def _fetch_into_scratch(checkout: Path, sha: str, scratch: Path) -> Path:
    remote = git(checkout, "remote", "get-url", "origin")
    if remote.returncode:
        raise PinUnavailable(f"checkout has no origin remote: {remote.stderr}")
    url = fetch_source(checkout, remote.stdout.strip())
    scratch.mkdir(parents=True)
    initialized = git(scratch, "init", "--quiet", "--bare")
    if initialized.returncode:
        raise PinUnavailable(initialized.stderr)
    # `--` keeps a URL that starts with `-` from being read as an option.
    fetched = git(scratch, "fetch", "--quiet", "--depth", "1", "--", url, sha)
    if fetched.returncode or not _has_commit(scratch, sha):
        raise PinUnavailable(f"cannot fetch {sha} from {url}: {fetched.stderr[-500:]}")
    return scratch


def _archive(source: Path, sha: str, archive: Path) -> subprocess.CompletedProcess:
    with archive.open("wb") as handle:
        return subprocess.run(
            ["git", "-C", str(source), "archive", sha],
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
            # Second guard: with GIT_NO_LAZY_FETCH=1, git refuses to fetch a
            # missing object from a promisor remote (git 2.43 does), so
            # `archive` fails instead of writing into the source repository.
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        )


def extract_pinned_tree(checkout: Path, sha: str, work: Path) -> Path:
    """Write the tree of ``sha`` to ``work/rulespec-us`` and return that path.

    Reads from ``checkout`` when it has the commit and is not a partial
    clone (``cat-file``, ``config --get``, ``config --get-regexp`` and
    ``archive`` then write nothing). Otherwise, or when that ``archive``
    fails (a checkout can still miss objects, e.g. a former partial clone
    whose promisor settings were removed but whose promisor pack remains),
    fetches the commit at depth 1 from the checkout's origin URL into
    ``work/scratch.git``, a bare repository that is not the checkout, and
    archives from there. ``work`` must be empty or absent.
    """

    # The directory name matters: the loader reads the country from
    # `rulespec-<country>`.
    root = work / "rulespec-us"
    root.mkdir(parents=True)
    archive = work / "tree.tar"
    scratch = work / "scratch.git"
    if is_partial_clone(checkout) or not _has_commit(checkout, sha):
        archived = _archive(_fetch_into_scratch(checkout, sha, scratch), sha, archive)
    else:
        archived = _archive(checkout, sha, archive)
        if archived.returncode:
            checkout_error = archived.stderr.decode(errors="replace")
            try:
                source = _fetch_into_scratch(checkout, sha, scratch)
            except PinUnavailable as error:
                # The checkout has the commit but cannot produce its tree:
                # report both failures rather than skip.
                raise AssertionError(
                    f"git archive failed in the checkout:\n{checkout_error}\n"
                    f"and the scratch fetch failed: {error}"
                ) from error
            archived = _archive(source, sha, archive)
    if archived.returncode:
        raise AssertionError(archived.stderr.decode(errors="replace"))
    with tarfile.open(archive) as tar:
        tar.extractall(root, filter="data")
    archive.unlink()
    return root


def git_dir_snapshot(repo: Path) -> dict[str, tuple[str, int]]:
    """(sha256, mtime_ns) of every file under ``repo/.git``, keyed by path.

    Reads the filesystem only and runs no git command, so taking it cannot
    itself refresh the index or write anything else.
    """

    git_dir = repo / ".git"
    snapshot = {
        str(path.relative_to(git_dir)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(git_dir.rglob("*"))
        if path.is_file()
    }
    # A worktree's `.git` is a file, which would give an empty snapshot that
    # compares equal whatever happens.
    assert snapshot, f"{git_dir} holds no files; is it a worktree's .git file?"
    return snapshot
