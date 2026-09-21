"""Shared test-fixture path resolution.

The real P&ID drawings, industrial line-list datasets and manual-tracing PDFs
live under the repository root -- e.g. ``./Contoh P&ID``, ``./combined_dataset``
and ``./backend/tests/fixtures/manual-tracing``.

Tests run in two very different environments:

* On the host, ``tests/`` sits inside ``<repo>/backend`` so the repository root
  is ``<backend_dir>/..`` and fixtures are at ``<repo>/backend/tests/...``.
* Inside the Docker ``api``/``worker`` container, ``docker-compose.yml`` bind
  mounts ``./backend`` onto ``/app``. The repository root therefore collapses to
  ``/`` while the fixtures are actually available next to the code at
  ``/app/Contoh P&ID``, ``/app/combined_dataset`` and ``/app/tests/...``.

Resolving fixtures with a single hard-coded root breaks one of the two
environments. ``fixture_path`` probes a list of candidate roots (and also the
same relative path with a redundant leading ``backend`` segment removed, so that
repo-relative paths like ``backend/tests/fixtures/...`` still work in the
container) and returns the first existing match. If nothing matches, the primary
candidate is returned so the usual ``assert path.exists()`` produces an
actionable error message.
"""
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _TESTS_DIR.parent

# Candidate roots, most specific first. ``_BACKEND_DIR``/``/app`` make the
# container layout (``/app/...``) resolvable alongside the host repo root.
_CANDIDATE_ROOTS = [
    _BACKEND_DIR.parent,   # host repo root: <repo>
    _BACKEND_DIR,          # container: /app  (and host fallback <repo>/backend)
    Path("/app"),          # explicit container path
    Path.cwd(),
]


def _candidate_parts(parts):
    """Yield the given parts plus a variant with a leading ``backend`` removed."""
    variants = [tuple(parts)]
    if parts and parts[0] == "backend":
        variants.append(tuple(parts[1:]))
    return variants


def fixture_path(*parts) -> Path:
    """Return the first existing candidate path for a fixture.

    ``parts`` are joined onto each candidate root, e.g.
    ``fixture_path("Contoh P&ID", "sheet.png")``. If none of the candidates
    exist the primary candidate is returned so callers can still assert on it.
    """
    primary = None
    for part_variant in _candidate_parts(parts):
        for root in _CANDIDATE_ROOTS:
            candidate = root.joinpath(*part_variant)
            if primary is None:
                primary = candidate
            if candidate.exists():
                return candidate
    # Nothing matched: return the primary candidate, giving the caller a
    # deterministic path to report in its assertion message.
    return primary if primary is not None else Path(*parts)
