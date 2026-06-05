"""Tiny in-memory async-Mongo fakes used by PR-2 unit tests.

We don't pull in mongomock-motor as a dep; this fake covers exactly the
calls `split_customers.py`, `CustomerRepository`, and `CustomerService`
reach for. Anything outside this surface raises `NotImplementedError`
loudly so tests fail fast on accidental real calls.
"""

from __future__ import annotations

import copy
from typing import Any


def _matches(doc: dict, flt: dict | None) -> bool:
    if not flt:
        return True
    for key, expected in flt.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, sub) for sub in expected):
                return False
            continue
        actual = doc
        for part in key.split("."):
            if not isinstance(actual, dict):
                actual = None
                break
            actual = actual.get(part)
        if isinstance(expected, dict):
            for op, val in expected.items():
                if op == "$in":
                    if actual not in val:
                        return False
                elif op == "$lte":
                    if actual is None or actual > val:
                        return False
                elif op == "$gte":
                    if actual is None or actual < val:
                        return False
                elif op == "$lt":
                    if actual is None or actual >= val:
                        return False
                elif op == "$gt":
                    if actual is None or actual <= val:
                        return False
                elif op == "$ne":
                    if actual == val:
                        return False
                elif op == "$exists":
                    # Walk the dotted path looking for *presence*, not value.
                    path = doc
                    found = True
                    for part in key.split("."):
                        if isinstance(path, dict) and part in path:
                            path = path[part]
                        else:
                            found = False
                            break
                    if found != bool(val):
                        return False
                else:
                    raise NotImplementedError(f"_fakes does not support {op}")
        elif actual != expected:
            return False
    return True


def _apply_set_path(
    doc: dict, path: str, value, array_filters: list[dict]
) -> None:
    """Apply a `$set` to a possibly-positional path.

    Supports paths like:
      - "field"                 → top-level set
      - "field.subfield"        → nested set
      - "arr.$[c].field"        → set via array_filters identifier `c`

    array_filters is a list of dicts like `[{"c.case_id": "x"}]`.
    """
    if "$[" not in path:
        # Plain or dotted; navigate creating dicts as needed.
        parts = path.split(".")
        cur = doc
        for p in parts[:-1]:
            if not isinstance(cur, dict):
                return
            cur = cur.setdefault(p, {})
        if isinstance(cur, dict):
            cur[parts[-1]] = copy.deepcopy(value)
        return
    # Positional with array_filters. We support exactly one $[ident] segment.
    head, _, rest = path.partition(".$[")
    ident, _, tail = rest.partition("].")
    if not tail:
        return
    arr = doc.get(head)
    if not isinstance(arr, list):
        return
    # Build a per-identifier filter dict.
    ident_filter: dict = {}
    for af in array_filters:
        for k, v in af.items():
            if k.startswith(f"{ident}."):
                ident_filter[k[len(ident) + 1:]] = v
    for item in arr:
        if isinstance(item, dict) and _matches(item, ident_filter):
            _apply_set_path(item, tail, value, array_filters)


def _project(doc: dict, projection: dict | None) -> dict:
    if not projection:
        return copy.deepcopy(doc)
    explicit_includes = {k for k, v in projection.items() if v == 1 and k != "_id"}
    drop_id = projection.get("_id", 1) == 0
    if explicit_includes:
        out = {k: copy.deepcopy(doc[k]) for k in explicit_includes if k in doc}
    else:
        out = copy.deepcopy(doc)
    if drop_id:
        out.pop("_id", None)
    return out


class _UpdateResult:
    def __init__(self, matched: int, modified: int, upserted_id: Any = None) -> None:
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id


class _DeleteResult:
    def __init__(self, deleted: int) -> None:
        self.deleted_count = deleted


class _Cursor:
    def __init__(self, docs: list[dict], projection: dict | None = None) -> None:
        self._docs = docs
        self._projection = projection
        self._sort_key: tuple[str, int] | None = None
        self._skip = 0
        self._limit: int | None = None

    def sort(self, spec):
        # spec like [("name", 1)] or list of one tuple
        key, direction = spec[0]
        self._sort_key = (key, direction)
        return self

    def skip(self, n: int):
        self._skip = n
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def _materialise(self) -> list[dict]:
        out = list(self._docs)
        if self._sort_key:
            key, direction = self._sort_key
            out.sort(key=lambda d: d.get(key) or "", reverse=direction < 0)
        if self._skip:
            out = out[self._skip:]
        # MongoDB convention: limit(0) means "no limit"; only positive
        # values cap the cursor.
        if self._limit:
            out = out[: self._limit]
        return [_project(d, self._projection) for d in out]

    def __aiter__(self):
        self._iter = iter(self._materialise())
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

    async def close(self) -> None:
        pass


class FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self._docs: list[dict] = []

    # --- internals ----------------------------------------------------

    def _index_of(self, flt: dict) -> int | None:
        for i, d in enumerate(self._docs):
            if _matches(d, flt):
                return i
        return None

    # --- read API -----------------------------------------------------

    def find(self, flt: dict | None = None, projection: dict | None = None, **_kw) -> _Cursor:
        matched = [copy.deepcopy(d) for d in self._docs if _matches(d, flt)]
        return _Cursor(matched, projection)

    async def find_one(
        self,
        flt: dict | None = None,
        projection: dict | None = None,
        *,
        sort: list[tuple[str, int]] | None = None,
    ) -> dict | None:
        # Optional `sort` kwarg matches PyMongo's signature so callers
        # that need "find the row with max(field)" can avoid manually
        # building a cursor. Only the first sort tuple is honoured.
        if sort:
            key, direction = sort[0]
            ordered = sorted(
                self._docs,
                key=lambda d: d.get(key) or "",
                reverse=direction < 0,
            )
            for d in ordered:
                if _matches(d, flt):
                    return _project(d, projection)
            return None
        for d in self._docs:
            if _matches(d, flt):
                return _project(d, projection)
        return None

    async def count_documents(self, flt: dict | None = None) -> int:
        return sum(1 for d in self._docs if _matches(d, flt))

    # --- write API ----------------------------------------------------

    async def insert_one(self, doc: dict):
        self._docs.append(copy.deepcopy(doc))
        result = _UpdateResult(1, 0)
        # PyMongo's `InsertOneResult` exposes `inserted_id`; BaseRepository
        # reads that attribute. Surface it on our shared result type so
        # FakeDB-backed tests can exercise `BaseRepository.insert_one`.
        result.inserted_id = doc.get("_id")
        return result

    async def update_one(
        self,
        flt: dict,
        update: dict,
        upsert: bool = False,
        array_filters: list[dict] | None = None,
    ) -> _UpdateResult:
        idx = self._index_of(flt)
        if idx is None:
            if upsert:
                new: dict = {}
                for k, v in flt.items():
                    if "." not in k and not isinstance(v, dict):
                        new[k] = v
                set_doc = update.get("$set") or {}
                for k, v in set_doc.items():
                    new[k] = copy.deepcopy(v)
                # `$setOnInsert` only applies on the insert branch.
                soi_doc = update.get("$setOnInsert") or {}
                for k, v in soi_doc.items():
                    new.setdefault(k, copy.deepcopy(v))
                inc_doc = update.get("$inc") or {}
                for k, v in inc_doc.items():
                    new[k] = new.get(k, 0) + v
                push_doc = update.get("$push") or {}
                for k, v in push_doc.items():
                    if isinstance(v, dict) and "$each" in v:
                        new[k] = list(v["$each"])
                    else:
                        new[k] = [copy.deepcopy(v)]
                addtoset_doc = update.get("$addToSet") or {}
                for k, v in addtoset_doc.items():
                    new[k] = [copy.deepcopy(v)]
                self._docs.append(new)
                return _UpdateResult(0, 0, upserted_id="fake")
            return _UpdateResult(0, 0)
        d = self._docs[idx]
        set_doc = update.get("$set") or {}
        for k, v in set_doc.items():
            _apply_set_path(d, k, v, array_filters or [])
        inc_doc = update.get("$inc") or {}
        for k, v in inc_doc.items():
            if "." in k:
                # Dotted-path increment, e.g. "rag_relevance_feedback.positive".
                # Walk into nested dicts (creating empty sub-docs as needed)
                # then apply the increment on the leaf, defaulting to 0.
                parts = k.split(".")
                cur = d
                for p in parts[:-1]:
                    nxt = cur.get(p)
                    if not isinstance(nxt, dict):
                        nxt = {}
                        cur[p] = nxt
                    cur = nxt
                leaf = parts[-1]
                cur[leaf] = (cur.get(leaf) or 0) + v
            else:
                d[k] = d.get(k, 0) + v
        # $min / $max: same dotted-path semantics as $inc but pick the
        # extremum between the existing leaf value and the new value.
        for op_name, picker in (("$min", min), ("$max", max)):
            op_doc = update.get(op_name) or {}
            for k, v in op_doc.items():
                if "." in k:
                    parts = k.split(".")
                    cur = d
                    for p in parts[:-1]:
                        nxt = cur.get(p)
                        if not isinstance(nxt, dict):
                            nxt = {}
                            cur[p] = nxt
                        cur = nxt
                    leaf = parts[-1]
                    existing = cur.get(leaf)
                    cur[leaf] = v if existing is None else picker(existing, v)
                else:
                    existing = d.get(k)
                    d[k] = v if existing is None else picker(existing, v)
        push_doc = update.get("$push") or {}
        for k, v in push_doc.items():
            # Resolve a dotted path so callers can $push into nested
            # arrays (e.g. "interaction_history.support_tickets").
            if "." in k:
                parts = k.split(".")
                cur = d
                for p in parts[:-1]:
                    nxt = cur.get(p)
                    if not isinstance(nxt, dict):
                        nxt = {}
                        cur[p] = nxt
                    cur = nxt
                leaf = parts[-1]
                current = list(cur.get(leaf) or [])
            else:
                cur = d
                leaf = k
                current = list(d.get(k) or [])
            if isinstance(v, dict) and "$each" in v:
                each = list(v["$each"])
                position = v.get("$position", len(current))
                slice_n = v.get("$slice")
                current = current[:position] + each + current[position:]
                if slice_n is not None:
                    current = current[:slice_n] if slice_n >= 0 else current[slice_n:]
            else:
                current.append(copy.deepcopy(v))
            cur[leaf] = current
        addtoset_doc = update.get("$addToSet") or {}
        for k, v in addtoset_doc.items():
            current = list(d.get(k) or [])
            if v not in current:
                current.append(v)
            d[k] = current
        pull_doc = update.get("$pull") or {}
        for k, v in pull_doc.items():
            current = list(d.get(k) or [])
            if isinstance(v, dict):
                # $pull with a sub-filter (e.g. {"case_id": "x"})
                d[k] = [item for item in current if not _matches(item, v)]
            else:
                d[k] = [item for item in current if item != v]
        return _UpdateResult(1, 1)

    async def replace_one(self, flt: dict, doc: dict, upsert: bool = False) -> _UpdateResult:
        idx = self._index_of(flt)
        if idx is None:
            if upsert:
                self._docs.append(copy.deepcopy(doc))
                return _UpdateResult(0, 0, upserted_id="fake")
            return _UpdateResult(0, 0)
        self._docs[idx] = copy.deepcopy(doc)
        return _UpdateResult(1, 1)

    async def delete_one(self, flt: dict) -> _DeleteResult:
        idx = self._index_of(flt)
        if idx is None:
            return _DeleteResult(0)
        self._docs.pop(idx)
        return _DeleteResult(1)

    async def bulk_write(self, ops, ordered: bool = False):
        matched = modified = 0
        for op in ops:
            # We only need UpdateOne support here.
            r = await self.update_one(op._doc[0] if hasattr(op, "_doc") else {}, op._doc[1] if hasattr(op, "_doc") else {})
            matched += r.matched_count
            modified += r.modified_count

        class _Res:
            def __init__(self) -> None:
                self.matched_count = matched
                self.modified_count = modified

        return _Res()


class FakeDB:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(name)
        return self._collections[name]

    async def drop_collection(self, name: str) -> None:
        self._collections.pop(name, None)
