"""Compatibility shim for message metadata aggregation."""

from __future__ import annotations

from agent_driver.observability import (
    aggregate_message_metadata_from_events,
    merge_message_metadata,
)

aggregate_metadata_from_events = aggregate_message_metadata_from_events
merge_metadata = merge_message_metadata

__all__ = ["aggregate_metadata_from_events", "merge_metadata"]
