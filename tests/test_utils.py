import pytest

from cs.client import check_key, cs_encode, strtobool, transform


class TestTransform:
    def test_scalars_are_stringified(self):
        params = {"a": 1, "b": "foo", "c": b"bar", "d": True}
        transform(params)
        # booleans are ints as far as Python is concerned
        assert params == {"a": "1", "b": "foo", "c": b"bar", "d": "True"}

    def test_none_values_are_dropped(self):
        params = {"a": None, "b": "foo"}
        transform(params)
        assert params == {"b": "foo"}

    def test_empty_containers_are_dropped(self):
        params = {"a": [], "b": {}, "c": (), "d": set(), "e": "keep"}
        transform(params)
        assert params == {"e": "keep"}

    @pytest.mark.parametrize("value", [["eggs", "spam"], ("eggs", "spam"), {"eggs"}])
    def test_sequences_are_joined(self, value):
        params = {"a": value}
        transform(params)
        assert params["a"] in ("eggs,spam", "eggs")

    def test_dict_becomes_an_indexed_map(self):
        params = {"d": {"key": "value"}}
        transform(params)
        assert params == {"d[0].key": "value"}

    def test_list_of_dicts_becomes_an_indexed_map(self):
        params = {"d": [{"key": "value"}, {"key": 42}]}
        transform(params)
        assert params == {"d[0].key": "value", "d[1].key": "42"}

    def test_unsupported_type(self):
        with pytest.raises(ValueError, match="float"):
            transform({"a": 4.2})


class TestCsEncode:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("a b", "a%20b"),
            ("a*b", "a*b"),
            ("a/b", "a%2Fb"),
            ("éè", "%C3%A9%C3%A8"),
        ],
    )
    def test_encoding(self, value, expected):
        assert cs_encode(value) == expected


class TestCheckKey:
    def test_exact_match(self):
        assert check_key("verify", {"verify", "cert"})

    def test_pattern_match(self):
        assert check_key("header_x-custom", {"header_*"})

    def test_no_match(self):
        assert not check_key("nope", {"verify", "header_*"})


class TestStrToBool:
    @pytest.mark.parametrize("value", ["y", "YES", "t", "true", "on", "1"])
    def test_true_values(self, value):
        assert strtobool(value) is True

    @pytest.mark.parametrize("value", ["n", "NO", "f", "false", "off", "0"])
    def test_false_values(self, value):
        assert strtobool(value) is False

    def test_invalid_value(self):
        with pytest.raises(ValueError, match="invalid truth value"):
            strtobool("maybe")
