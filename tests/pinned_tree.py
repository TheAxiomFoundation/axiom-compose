"""Extract a rulespec checkout's tree at a pinned commit, read-only.

The checkout a developer points ``AXIOM_RULESPEC_US_ROOT`` at must never be
written to. ``git fetch --depth 1`` into a full clone writes ``.git/shallow``
and grafts the fetched commit as a parentless root, which breaks later
merges there. So when the checkout lacks the pin, the commit is fetched into
a throwaway repository instead, and the tree is archived from that.
"""

from __future__ import annotations

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


def _fetch_into_scratch(checkout: Path, sha: str, scratch: Path) -> Path:
    remote = git(checkout, "remote", "get-url", "origin")
    if remote.returncode:
        raise PinUnavailable(f"checkout has no origin remote: {remote.stderr}")
    url = remote.stdout.strip()
    scratch.mkdir(parents=True)
    initialized = git(scratch, "init", "--quiet", "--bare")
    if initialized.returncode:
        raise PinUnavailable(initialized.stderr)
    fetched = git(scratch, "fetch", "--quiet", "--depth", "1", url, sha)
    if fetched.returncode or not _has_commit(scratch, sha):
        raise PinUnavailable(f"cannot fetch {sha} from {url}: {fetched.stderr[-500:]}")
    return scratch


def extract_pinned_tree(checkout: Path, sha: str, work: Path) -> Path:
    """Write the tree of ``sha`` to ``work/rulespec-us`` and return that path.

    Reads from ``checkout`` when it has the commit (``cat-file`` and
    ``archive`` write nothing). Otherwise fetches the commit at depth 1 from
    the checkout's origin URL into ``work/scratch.git``, a bare repository
    that is not the checkout. ``work`` must be empty or absent.
    """

    source = checkout
    if not _has_commit(checkout, sha):
        source = _fetch_into_scratch(checkout, sha, work / "scratch.git")
    # The directory name matters: the loader reads the country from
    # `rulespec-<country>`.
    root = work / "rulespec-us"
    root.mkdir(parents=True)
    archive = work / "tree.tar"
    with archive.open("wb") as handle:
        archived = subprocess.run(
            ["git", "-C", str(source), "archive", sha],
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if archived.returncode:
        raise AssertionError(archived.stderr.decode(errors="replace"))
    with tarfile.open(archive) as tar:
        tar.extractall(root, filter="data")
    archive.unlink()
    return root


def repository_state(repo: Path) -> dict[str, object]:
    """What the pinned fixture must leave unchanged in a checkout."""

    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    shallow = git_dir / "shallow"
    return {
        "is_shallow": git(repo, "rev-parse", "--is-shallow-repository").stdout.strip(),
        "shallow_file": shallow.read_text() if shallow.exists() else None,
        "refs": git(repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout,
        "head": git(repo, "rev-parse", "HEAD").stdout.strip(),
        "status": git(repo, "status", "--porcelain").stdout,
    }
