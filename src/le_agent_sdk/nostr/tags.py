"""Tag parsing and building utilities for Nostr events."""

from __future__ import annotations

from typing import Any, Optional


class TagParser:
    """Utility class for parsing and building Nostr event tags."""

    @staticmethod
    def get_tag_value(tags: list[list[str]], key: str) -> Optional[str]:
        """Get the first value for a given tag key.

        Args:
            tags: List of Nostr tags (each tag is a list of strings).
            key: Tag key to search for (e.g. "d", "e", "p").

        Returns:
            The first value (index 1) for the matching tag, or None.
        """
        for tag in tags:
            if len(tag) >= 2 and tag[0] == key:
                return tag[1]
        return None

    @staticmethod
    def get_tag_values(tags: list[list[str]], key: str) -> list[str]:
        """Get all values for a given tag key.

        Args:
            tags: List of Nostr tags.
            key: Tag key to search for.

        Returns:
            List of values (index 1) for all matching tags.
        """
        return [tag[1] for tag in tags if len(tag) >= 2 and tag[0] == key]

    @staticmethod
    def get_full_tags(tags: list[list[str]], key: str) -> list[list[str]]:
        """Get all complete tags matching a key.

        Args:
            tags: List of Nostr tags.
            key: Tag key to search for.

        Returns:
            List of complete tag arrays matching the key.
        """
        return [tag for tag in tags if tag and tag[0] == key]

    @staticmethod
    def has_tag(tags: list[list[str]], key: str, value: Optional[str] = None) -> bool:
        """Check if a tag exists, optionally matching a specific value.

        Args:
            tags: List of Nostr tags.
            key: Tag key to check.
            value: Optional value to match (index 1).

        Returns:
            True if a matching tag exists.
        """
        for tag in tags:
            if not tag:
                continue
            if tag[0] == key:
                if value is None:
                    return True
                if len(tag) >= 2 and tag[1] == value:
                    return True
        return False

    @staticmethod
    def build_filter(
        kinds: Optional[list[int]] = None,
        authors: Optional[list[str]] = None,
        ids: Optional[list[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
        tags: Optional[dict[str, list[str]]] = None,
    ) -> dict[str, Any]:
        """Build a Nostr filter dict for REQ subscriptions.

        Args:
            kinds: Event kinds to filter.
            authors: Author pubkeys to filter.
            ids: Event IDs to filter.
            since: Minimum created_at timestamp.
            until: Maximum created_at timestamp.
            limit: Maximum number of events.
            tags: Tag filters as {key: [values]}, e.g. {"#t": ["ai"]}.

        Returns:
            Nostr filter dict ready for use in REQ messages.
        """
        f: dict[str, Any] = {}

        if kinds is not None:
            f["kinds"] = kinds
        if authors is not None:
            f["authors"] = authors
        if ids is not None:
            f["ids"] = ids
        if since is not None:
            f["since"] = since
        if until is not None:
            f["until"] = until
        if limit is not None:
            f["limit"] = limit
        if tags:
            for key, values in tags.items():
                # Nostr filters use #<tag-letter> for tag queries
                filter_key = key if key.startswith("#") else f"#{key}"
                f[filter_key] = values

        return f

    @staticmethod
    def merge_tags(
        base: list[list[str]], additions: list[list[str]]
    ) -> list[list[str]]:
        """Merge two tag lists, avoiding exact duplicates.

        Args:
            base: Base tag list.
            additions: Tags to add.

        Returns:
            Combined tag list without exact duplicates.
        """
        seen: set[tuple[str, ...]] = set()
        result: list[list[str]] = []

        for tag in base + additions:
            key = tuple(tag)
            if key not in seen:
                seen.add(key)
                result.append(tag)

        return result
