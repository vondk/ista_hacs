"""Resolve display names for meters.

The CSV's ``Rum`` column is not a reliable identity: a typical account has
several meters called "Værelse" and some with no room at all ("?"). Statistics,
devices and entities must all agree on one name per meter, so both
:mod:`.statistics` and :mod:`.sensor` build their names through here.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .api import Reading

# Room values that carry no information and should fall back to the meter id.
_PLACEHOLDER_ROOMS = {"", "?", "-", "n/a"}


def _base_name(meter_id: str, room: str, alias: str | None) -> str:
    """Pick the preferred name for one meter, before deduplication."""
    if alias and alias.strip():
        return alias.strip()
    room = (room or "").strip()
    if room.lower() in _PLACEHOLDER_ROOMS:
        return meter_id
    return room


def resolve_meter_names(
    readings: Iterable[Reading], aliases: dict[str, str] | None = None
) -> dict[str, str]:
    """Return a ``{meter_id: display name}`` map with unique names.

    A user-supplied alias wins, then the room from the CSV (last one seen for
    that meter), then the meter id. Names shared by several meters get the
    meter id appended so devices and statistics stay distinguishable.
    """
    aliases = aliases or {}

    rooms: dict[str, str] = {}
    for reading in readings:
        rooms[reading.meter_id] = reading.room

    # Aliases may name a meter that has no readings yet; keep it anyway.
    meter_ids = sorted(set(rooms) | {mid for mid, name in aliases.items() if name})

    base: dict[str, str] = {
        meter_id: _base_name(meter_id, rooms.get(meter_id, ""), aliases.get(meter_id))
        for meter_id in meter_ids
    }

    by_name: dict[str, list[str]] = defaultdict(list)
    for meter_id, name in base.items():
        by_name[name].append(meter_id)

    return {
        meter_id: (name if len(by_name[name]) == 1 else f"{name} ({meter_id})")
        for meter_id, name in base.items()
    }
