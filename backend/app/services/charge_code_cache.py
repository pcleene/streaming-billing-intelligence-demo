"""In-memory charge code cache (PR-5).

The catalog is small and read on every transaction line. We load the
full set at app startup (or first miss), serve `get` / `validate` from
process memory, and have a change-stream watcher (`charge_code_change_watcher`)
hot-patch the cache when an admin edits a code via the repo.

The cache is intentionally process-local: every uvicorn worker / consumer
process keeps its own copy. The watcher's job is simply to keep the
copies coherent within a couple of seconds of an edit.

Degraded mode: when the cache has not yet been loaded (e.g. tests that
construct a BatchCache without booting the full app), `validate` returns
`True` and callers continue to log unknown-code warnings at the call
site. This matches the pre-PR-5 behaviour and keeps PR-3's contract
intact.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.repositories.charge_code_repo import ChargeCodeRepository

logger = get_logger(__name__)


class ChargeCodeCache:
    """Process-local dict of `code -> doc`. Thread / async-task safe by
    construction (single owner; mutations are simple pointer swaps)."""

    def __init__(self) -> None:
        self._codes: dict[str, dict] = {}
        self._loaded: bool = False

    # --- lifecycle ----------------------------------------------------

    async def load(self, repo: "ChargeCodeRepository") -> int:
        docs = await repo.list_all()
        self._codes = {d["code"]: copy.deepcopy(d) for d in docs if d.get("code")}
        self._loaded = True
        logger.info("charge_code_cache_loaded", count=len(self._codes))
        return len(self._codes)

    def clear(self) -> None:
        """Reset to the unloaded state. Tests use this between fixtures."""
        self._codes = {}
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def size(self) -> int:
        return len(self._codes)

    # --- read ---------------------------------------------------------

    def get(self, code: str | None) -> dict | None:
        if not code:
            return None
        doc = self._codes.get(code)
        return copy.deepcopy(doc) if doc is not None else None

    def validate(self, code: str | None) -> bool:
        """Return True iff `code` resolves to a non-deprecated entry.

        Falls back to True when the cache has not yet been loaded so the
        write path doesn't block on cold-start. Callers should still log
        a warning at the unknown-code site for observability.
        """
        if code is None:
            return False
        if not self._loaded:
            return True
        doc = self._codes.get(code)
        if doc is None:
            return False
        return not doc.get("deprecated", False)

    def all_codes(self) -> list[str]:
        return sorted(self._codes.keys())

    # --- mutation (driven by change-stream watcher) -------------------

    def upsert(self, doc: dict) -> None:
        """Apply a single insert/update from the change stream."""
        code = doc.get("code")
        if not code:
            return
        self._codes[code] = copy.deepcopy(doc)

    def remove(self, code: str) -> None:
        self._codes.pop(code, None)


# --- module-level singleton -----------------------------------------------
charge_code_cache = ChargeCodeCache()
