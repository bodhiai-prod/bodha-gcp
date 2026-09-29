"""Small async MongoDB-shaped adapter backed by Google Cloud Firestore.

The API layer was originally written for Motor.  Keeping its narrow query
surface here lets the application move to Firestore without changing routes,
response shapes, or background jobs.  Documents remain application-owned
JSON with the existing ``id`` fields; Firestore document IDs are internal.

Complex legacy filters continue to be evaluated in the application.  Simple
equality and ``$in`` filters, which cover hot detail/status reads, are pushed
to Firestore so a textbook lookup does not scan the whole collection.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Any, AsyncIterator, Iterable

from google.cloud import firestore
from google.cloud.firestore_v1.async_client import AsyncClient


_MISSING = object()


def _encode_generation_requirements(document: dict[str, Any]) -> dict[str, Any]:
    """Make the template snapshot safe for Firestore persistence.

    Firestore rejects nested arrays.  A slot-aware blueprint has
    ``sections -> groups -> slots`` and some templates additionally express
    disjoint section pairs as a list of two-item lists.  The application uses
    those lists in memory, so encode only the persisted copy and restore it at
    the collection read boundary below.
    """
    encoded = copy.deepcopy(document)
    requirements = encoded.get("generation_requirements")
    blueprint = requirements.get("blueprint") if isinstance(requirements, dict) else None
    if not isinstance(blueprint, dict):
        return encoded

    for section in blueprint.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for group in section.get("groups") or []:
            if not isinstance(group, dict) or not isinstance(group.get("slots"), list):
                continue
            group["slots"] = {
                str(slot.get("id", index)): slot
                for index, slot in enumerate(group["slots"])
                if isinstance(slot, dict)
            }

    pairs = blueprint.get("disjoint_section_pairs")
    if isinstance(pairs, list):
        blueprint["disjoint_section_pairs"] = [
            {"first": pair[0], "second": pair[1]}
            for pair in pairs
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        ]
    return encoded


def _decode_generation_requirements(document: dict[str, Any]) -> dict[str, Any]:
    """Restore Firestore-safe blueprint fields to the application's list form."""
    decoded = copy.deepcopy(document)
    requirements = decoded.get("generation_requirements")
    blueprint = requirements.get("blueprint") if isinstance(requirements, dict) else None
    if not isinstance(blueprint, dict):
        return decoded

    for section in blueprint.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for group in section.get("groups") or []:
            if isinstance(group, dict) and isinstance(group.get("slots"), dict):
                group["slots"] = list(group["slots"].values())

    pairs = blueprint.get("disjoint_section_pairs")
    if isinstance(pairs, list) and all(isinstance(pair, dict) for pair in pairs):
        blueprint["disjoint_section_pairs"] = [
            [pair["first"], pair["second"]]
            for pair in pairs
            if "first" in pair and "second" in pair
        ]
    return decoded


def _get(document: dict[str, Any], path: str, default: Any = _MISSING) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def _set(document: dict[str, Any], path: str, value: Any) -> None:
    target = document
    parts = path.split(".")
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            child = {}
            target[part] = child
        target = child
    target[parts[-1]] = value


def _matches_value(value: Any, condition: Any) -> bool:
    if not isinstance(condition, dict) or not any(str(k).startswith("$") for k in condition):
        return value is not _MISSING and value == condition
    for operator, expected in condition.items():
        if operator == "$in":
            if value is _MISSING or value not in expected:
                return False
        elif operator == "$ne":
            if value is not _MISSING and value == expected:
                return False
        elif operator == "$lt":
            if value is _MISSING or value >= expected:
                return False
        elif operator == "$lte":
            if value is _MISSING or value > expected:
                return False
        elif operator == "$gt":
            if value is _MISSING or value <= expected:
                return False
        elif operator == "$gte":
            if value is _MISSING or value < expected:
                return False
        elif operator == "$exists":
            if (value is not _MISSING) != bool(expected):
                return False
        else:
            raise ValueError(f"Unsupported Firestore compatibility operator: {operator}")
    return True


def _matches(document: dict[str, Any], query: dict[str, Any] | None) -> bool:
    for key, condition in (query or {}).items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in condition):
                return False
        elif key == "$and":
            if not all(_matches(document, branch) for branch in condition):
                return False
        elif not _matches_value(_get(document, key), condition):
            return False
    return True


def _project(document: dict[str, Any], projection: dict[str, int] | None) -> dict[str, Any]:
    result = copy.deepcopy(document)
    if not projection:
        return result
    included = [key for key, value in projection.items() if value and key != "_id"]
    if included:
        projected: dict[str, Any] = {}
        for key in included:
            value = _get(result, key)
            if value is not _MISSING:
                _set(projected, key, value)
        return projected
    for key, value in projection.items():
        if not value:
            result.pop(key, None)
    return result


@dataclass
class _WriteResult:
    modified_count: int = 0
    deleted_count: int = 0


class FirestoreCursor:
    def __init__(self, collection: "FirestoreCollection", query: dict[str, Any] | None, projection: dict[str, int] | None):
        self._collection = collection
        self._query = query
        self._projection = projection
        self._sort: list[tuple[str, int]] = []

    def sort(self, key: str, direction: int) -> "FirestoreCursor":
        self._sort.append((key, direction))
        return self

    async def _documents(self) -> list[dict[str, Any]]:
        documents = [doc for doc in await self._collection._all_documents() if _matches(doc, self._query)]

        def compare(left: dict[str, Any], right: dict[str, Any]) -> int:
            for key, direction in self._sort:
                a, b = _get(left, key, None), _get(right, key, None)
                if a == b:
                    continue
                # The app sorts ISO timestamps and strings.  Normalising None
                # prevents a missing optional field from crashing a list page.
                result = -1 if (a is None or (b is not None and a < b)) else 1
                return result if direction >= 0 else -result
            return 0

        if self._sort:
            documents.sort(key=cmp_to_key(compare))
        return [_project(doc, self._projection) for doc in documents]

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        documents = await self._documents()
        return documents if length is None else documents[:length]

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def iterator() -> AsyncIterator[dict[str, Any]]:
            for document in await self._documents():
                yield document

        return iterator()


class FirestoreCollection:
    def __init__(self, client: AsyncClient, name: str):
        self._client = client
        self._collection = client.collection(name)

    async def _all_documents(self) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        async for snapshot in self._collection.stream():
            data = _decode_generation_requirements(snapshot.to_dict() or {})
            docs.append(data)
        return docs

    def _native_query(self, query: dict[str, Any] | None):
        """Return a Firestore query for safe simple filters, else ``None``.

        Preserve the compatibility layer's Python matcher for `$or`, range
        operators, and other Mongo-shaped expressions until those routes are
        converted deliberately.  This small fast path covers the most common
        `id` and status lookups without changing their result semantics.
        """
        if query is None:
            return self._collection
        native = self._collection
        for key, condition in query.items():
            if key.startswith("$"):
                return None
            if isinstance(condition, dict):
                if set(condition) == {"$in"} and isinstance(condition["$in"], list):
                    native = native.where(key, "in", condition["$in"])
                else:
                    return None
            else:
                native = native.where(key, "==", condition)
        return native

    async def _matching_snapshots(self, query: dict[str, Any] | None) -> list[tuple[Any, dict[str, Any]]]:
        matches: list[tuple[Any, dict[str, Any]]] = []
        native = self._native_query(query)
        source = native if native is not None else self._collection
        async for snapshot in source.stream():
            data = _decode_generation_requirements(snapshot.to_dict() or {})
            if _matches(data, query):
                matches.append((snapshot, data))
        return matches

    async def find_one(self, query: dict[str, Any] | None = None, projection: dict[str, int] | None = None) -> dict[str, Any] | None:
        matches = await self._matching_snapshots(query)
        return _project(matches[0][1], projection) if matches else None

    def find(self, query: dict[str, Any] | None = None, projection: dict[str, int] | None = None) -> FirestoreCursor:
        return FirestoreCursor(self, query, projection)

    async def insert_one(self, document: dict[str, Any]) -> _WriteResult:
        await self._collection.document(str(uuid.uuid4())).set(
            _encode_generation_requirements(document)
        )
        return _WriteResult(modified_count=1)

    async def insert_many(self, documents: Iterable[dict[str, Any]]) -> _WriteResult:
        batch = self._client.batch()
        count = 0
        for document in documents:
            batch.set(
                self._collection.document(str(uuid.uuid4())),
                _encode_generation_requirements(document),
            )
            count += 1
        if count:
            await batch.commit()
        return _WriteResult(modified_count=count)

    async def _apply_update(self, snapshot: Any, document: dict[str, Any], update: dict[str, Any]) -> None:
        updated = copy.deepcopy(document)
        for path, value in update.get("$set", {}).items():
            _set(updated, path, value)
        for path, value in update.get("$inc", {}).items():
            _set(updated, path, (_get(updated, path, 0) or 0) + value)
        unsupported = set(update) - {"$set", "$inc"}
        if unsupported:
            raise ValueError(f"Unsupported Firestore compatibility update: {sorted(unsupported)}")
        await snapshot.reference.set(_encode_generation_requirements(updated))

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> _WriteResult:
        matches = await self._matching_snapshots(query)
        if matches:
            await self._apply_update(matches[0][0], matches[0][1], update)
            return _WriteResult(modified_count=1)
        if upsert:
            document = {key: value for key, value in query.items() if not key.startswith("$") and not isinstance(value, dict)}
            for path, value in update.get("$set", {}).items():
                _set(document, path, value)
            await self.insert_one(document)
            return _WriteResult(modified_count=1)
        return _WriteResult()

    async def claim_one(self, query: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any] | None:
        """Atomically transition one matching document to a leased state.

        Queue consumers must not use a read followed by ``update_one``: two
        workers can observe the same queued document.  Firestore transactions
        give the lease an optimistic compare-and-set boundary.
        """
        matches = await self._matching_snapshots(query)
        for snapshot, _document in matches:
            transaction = self._client.transaction()
            try:
                fresh = await transaction.get(snapshot.reference)
                # firestore's async client returns a single snapshot for a
                # document reference in current releases; accept the legacy
                # one-item list shape too so queue leasing is version-safe.
                if isinstance(fresh, (list, tuple)):
                    fresh = fresh[0] if fresh else None
                if fresh is None:
                    continue
                current = _decode_generation_requirements(fresh.to_dict() or {}) if fresh.exists else None
                if not current or not _matches(current, query):
                    continue
                updated = copy.deepcopy(current)
                for path, value in fields.items():
                    _set(updated, path, value)
                transaction.set(snapshot.reference, _encode_generation_requirements(updated))
                await transaction.commit()
                return updated
            except Exception:
                # A concurrent winner invalidates this candidate.  The next
                # polling pass (or candidate) safely retries it.
                try:
                    await transaction.rollback()
                except Exception:
                    pass
                continue
        return None

    async def update_many(self, query: dict[str, Any], update: dict[str, Any]) -> _WriteResult:
        matches = await self._matching_snapshots(query)
        for snapshot, document in matches:
            await self._apply_update(snapshot, document, update)
        return _WriteResult(modified_count=len(matches))

    async def delete_many(self, query: dict[str, Any]) -> _WriteResult:
        matches = await self._matching_snapshots(query)
        for snapshot, _ in matches:
            await snapshot.reference.delete()
        return _WriteResult(deleted_count=len(matches))

    async def delete_one(self, query: dict[str, Any]) -> _WriteResult:
        matches = await self._matching_snapshots(query)
        if not matches:
            return _WriteResult()
        await matches[0][0].reference.delete()
        return _WriteResult(deleted_count=1)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return len(await self._matching_snapshots(query))

    async def create_index(self, *_args: Any, **_kwargs: Any) -> None:
        # Firestore creates single-field indexes automatically.  This adapter
        # evaluates compound filters locally, so no Mongo index translation is
        # required for correctness.
        return None


class FirestoreDatabase:
    def __init__(self, project: str, database: str = "(default)"):
        self._client = AsyncClient(project=project, database=database)
        self._collections: dict[str, FirestoreCollection] = {}

    def __getitem__(self, name: str) -> FirestoreCollection:
        return self._collections.setdefault(name, FirestoreCollection(self._client, name))

    def __getattr__(self, name: str) -> FirestoreCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    async def command(self, command: str) -> dict[str, int]:
        if command != "ping":
            raise ValueError(f"Unsupported Firestore database command: {command}")
        # Fetching a known collection proves ADC can call Firestore without
        # writing application data.
        await self._client.collection("users").limit(1).get()
        return {"ok": 1}


class FirestoreClient:
    """Drop-in lifecycle object retained for the FastAPI shutdown handler."""

    def close(self) -> None:
        return None
