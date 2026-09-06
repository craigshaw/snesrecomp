"""Load event-crossing audit reports as exact AOT precision profiles."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import FrozenSet, Tuple


PROFILE_ENV = "SNESRECOMP_EVENT_PRECISION_PROFILE"
PROFILE_SCHEMA = "snesrecomp event crossing audit v1"

EventPrecisionSite = Tuple[int, int, int]


def event_precision_profile_path() -> pathlib.Path | None:
    value = os.environ.get(PROFILE_ENV)
    return pathlib.Path(value).expanduser() if value else None


def event_precision_profile_digest() -> str:
    """Return a cache identity for the active profile and its contents."""
    path = event_precision_profile_path()
    if path is None:
        return "disabled"
    try:
        contents = path.read_bytes()
    except OSError as exc:
        return f"unreadable:{path}:{exc}"
    return hashlib.sha256(contents).hexdigest()


def load_event_precision_sites() -> FrozenSet[EventPrecisionSite]:
    """Return actionable ``(pc24, m, x)`` crossings from the active report."""
    path = event_precision_profile_path()
    if path is None:
        return frozenset()
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read event precision profile {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid event precision profile JSON {path}: {exc}") from exc

    if not isinstance(report, dict) or report.get("schema") != PROFILE_SCHEMA:
        raise ValueError(
            f"event precision profile {path} must use schema {PROFILE_SCHEMA!r}")
    overflow = report.get("overflow", 0)
    if not isinstance(overflow, int) or overflow < 0:
        raise ValueError(f"event precision profile {path} has invalid overflow")
    if overflow:
        raise ValueError(
            f"event precision profile {path} is incomplete: overflow={overflow}")
    entries = report.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"event precision profile {path} has no entries list")

    result = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(
                f"event precision profile {path} entry {index} is not an object")
        pc24 = entry.get("pc24")
        m = entry.get("m")
        x = entry.get("x")
        hits = entry.get("hits")
        irq_i_set_hits = entry.get("irq_i_set_hits", 0)
        event = entry.get("event")
        if not isinstance(pc24, int) or not 0 <= pc24 <= 0xFFFFFF:
            raise ValueError(
                f"event precision profile {path} entry {index} has invalid pc24")
        if m not in (0, 1) or x not in (0, 1):
            raise ValueError(
                f"event precision profile {path} entry {index} has invalid M/X")
        if not isinstance(hits, int) or hits < 0:
            raise ValueError(
                f"event precision profile {path} entry {index} has invalid hits")
        if not isinstance(irq_i_set_hits, int) or not 0 <= irq_i_set_hits <= hits:
            raise ValueError(
                f"event precision profile {path} entry {index} has invalid "
                "irq_i_set_hits")
        if event not in ("vblank", "nmi", "irq", "unknown"):
            raise ValueError(
                f"event precision profile {path} entry {index} has invalid event")

        # An IRQ cannot be delivered while I is set. A mixed tuple remains
        # actionable because at least one observation occurred with IRQ enabled.
        actionable_hits = hits - irq_i_set_hits if event == "irq" else hits
        if actionable_hits:
            result.add((pc24, m, x))
    return frozenset(result)
