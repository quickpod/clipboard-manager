"""Transform tests -- pure functions, no clipboard needed."""

import pytest

from clipkit import transforms
from clipkit.errors import ClipKitError


def test_case_transforms():
    assert transforms.upper("Hello") == "HELLO"
    assert transforms.lower("Hello") == "hello"
    assert transforms.title("hello there world") == "Hello There World"


def test_trim_and_collapse():
    assert transforms.trim("  padded  ") == "padded"
    assert transforms.collapse_whitespace("a   b\t\nc  ") == "a b c"


def test_join_and_remove_newlines():
    assert transforms.join_lines("one\ntwo\nthree") == "one two three"
    assert transforms.remove_newlines("a\r\nb\nc") == "abc"


def test_split_lines():
    assert transforms.split_lines("a, b  c;d") == "a\nb\nc\nd"


def test_base64_round_trip():
    original = "Hello, world! ☕ café"
    encoded = transforms.base64_encode(original)
    assert transforms.base64_decode(encoded) == original


def test_base64_decode_invalid_raises():
    with pytest.raises(ClipKitError):
        transforms.base64_decode("!!!not base64!!!")


def test_url_encode_decode_round_trip():
    original = "a b&c=d/e?f"
    encoded = transforms.url_encode(original)
    assert "%20" in encoded and "&" not in encoded
    assert transforms.url_decode(encoded) == original


def test_slugify():
    assert transforms.slugify("Héllo,  World!!") == "hello-world"
    assert transforms.slugify("  --Already-Slug--  ") == "already-slug"
    assert transforms.slugify("Café del Mar") == "cafe-del-mar"


def test_count():
    out = transforms.count("one two\nthree")
    assert "characters=13" in out
    assert "words=3" in out
    assert "lines=2" in out


def test_registry_apply_and_names():
    names = transforms.names()
    assert "base64-encode" in names and "slugify" in names
    # every registered transform is callable and returns a string; use an input
    # that is valid for the decoders too (plain ascii is valid url/base64-ish
    # only for base64 if padded, so feed each decoder its own valid text).
    valid = {
        "base64-decode": transforms.base64_encode("Sample Text 123"),
        "url-decode": transforms.url_encode("Sample Text 123"),
    }
    for name in names:
        text = valid.get(name, "Sample Text 123")
        assert isinstance(transforms.apply(name, text), str)


def test_apply_unknown_transform_raises():
    with pytest.raises(ClipKitError):
        transforms.apply("does-not-exist", "x")
