"""Tests for Nostr tag parsing and building utilities."""

from le_agent_sdk.nostr.tags import TagParser


class TestGetTagValue:
    def test_found(self):
        tags = [["d", "svc-1"], ["t", "ai"]]
        assert TagParser.get_tag_value(tags, "d") == "svc-1"

    def test_not_found(self):
        tags = [["d", "svc-1"]]
        assert TagParser.get_tag_value(tags, "t") is None

    def test_empty_tags(self):
        assert TagParser.get_tag_value([], "d") is None

    def test_first_match_wins(self):
        tags = [["t", "first"], ["t", "second"]]
        assert TagParser.get_tag_value(tags, "t") == "first"

    def test_short_tag_skipped(self):
        tags = [["d"]]
        assert TagParser.get_tag_value(tags, "d") is None


class TestGetTagValues:
    def test_multiple(self):
        tags = [["s", "ai"], ["s", "ml"], ["d", "svc"]]
        assert TagParser.get_tag_values(tags, "s") == ["ai", "ml"]

    def test_none_found(self):
        tags = [["d", "svc"]]
        assert TagParser.get_tag_values(tags, "s") == []


class TestGetFullTags:
    def test_returns_full_tags(self):
        tags = [["price", "10", "sats", "per-request"], ["d", "svc"]]
        price_tags = TagParser.get_full_tags(tags, "price")
        assert len(price_tags) == 1
        assert price_tags[0] == ["price", "10", "sats", "per-request"]

    def test_empty_result(self):
        assert TagParser.get_full_tags([], "price") == []


class TestHasTag:
    def test_key_only(self):
        tags = [["d", "svc"]]
        assert TagParser.has_tag(tags, "d") is True
        assert TagParser.has_tag(tags, "t") is False

    def test_key_and_value(self):
        tags = [["t", "ai"], ["t", "ml"]]
        assert TagParser.has_tag(tags, "t", "ai") is True
        assert TagParser.has_tag(tags, "t", "vision") is False

    def test_empty_tags(self):
        assert TagParser.has_tag([], "d") is False

    def test_empty_tag_in_list(self):
        tags = [[], ["d", "svc"]]
        assert TagParser.has_tag(tags, "d") is True


class TestBuildFilter:
    def test_kinds_only(self):
        f = TagParser.build_filter(kinds=[38400])
        assert f == {"kinds": [38400]}

    def test_full_filter(self):
        f = TagParser.build_filter(
            kinds=[38400, 38401],
            authors=["pub1"],
            limit=10,
            since=1700000000,
            tags={"s": ["ai"]},
        )
        assert f["kinds"] == [38400, 38401]
        assert f["authors"] == ["pub1"]
        assert f["limit"] == 10
        assert f["since"] == 1700000000
        assert f["#s"] == ["ai"]

    def test_tag_filter_auto_prefix(self):
        f = TagParser.build_filter(tags={"t": ["test"]})
        assert "#t" in f

    def test_tag_filter_preserves_hash(self):
        f = TagParser.build_filter(tags={"#t": ["test"]})
        assert "#t" in f

    def test_empty_filter(self):
        f = TagParser.build_filter()
        assert f == {}

    def test_ids_filter(self):
        f = TagParser.build_filter(ids=["abc123"])
        assert f["ids"] == ["abc123"]


class TestMergeTags:
    def test_no_duplicates(self):
        base = [["d", "svc"]]
        additions = [["t", "ai"]]
        result = TagParser.merge_tags(base, additions)
        assert len(result) == 2

    def test_dedup(self):
        base = [["t", "ai"], ["d", "svc"]]
        additions = [["t", "ai"], ["t", "ml"]]
        result = TagParser.merge_tags(base, additions)
        assert len(result) == 3
        t_values = [t[1] for t in result if t[0] == "t"]
        assert t_values == ["ai", "ml"]

    def test_empty(self):
        assert TagParser.merge_tags([], []) == []
