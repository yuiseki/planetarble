"""Tests for GSI mokuroku catalog parsing."""

from __future__ import annotations

from planetarble.acquisition.mokuroku import (
    MokurokuEntry,
    iter_mokuroku_lines,
    parse_mokuroku_line,
)


def test_parse_valid_line() -> None:
    e = parse_mokuroku_line("18/232854/103222.jpg,1583830819,12345,746c24fdfe98068ad4bcdf8cbda60957")
    assert e == MokurokuEntry(
        z=18, x=232854, y=103222, ext="jpg", mtime=1583830819, size=12345,
        md5="746c24fdfe98068ad4bcdf8cbda60957",
    )


def test_parse_blank_and_malformed_return_none() -> None:
    assert parse_mokuroku_line("") is None
    assert parse_mokuroku_line("  \n") is None
    assert parse_mokuroku_line("garbage") is None
    assert parse_mokuroku_line("10/848.jpg,1,2") is None  # not z/x/y
    assert parse_mokuroku_line("a/b/c.jpg,1,2") is None   # non-int coords


def test_parse_tolerates_missing_md5() -> None:
    e = parse_mokuroku_line("8/100/200.jpg,1583830819,1053")
    assert e is not None and e.z == 8 and e.md5 == ""


def test_iter_filters_by_zoom_range() -> None:
    lines = [
        "7/1/1.jpg,1,1,a",
        "8/1/1.jpg,1,1,b",
        "16/1/1.jpg,1,1,c",
        "17/1/1.jpg,1,1,d",
        "",                       # blank -> skipped
        "junk",                   # malformed -> skipped
        "16/2/2.jpg,1,1,e",
    ]
    got = list(iter_mokuroku_lines(lines, zoom_min=8, zoom_max=16))
    assert [(e.z, e.x, e.y) for e in got] == [(8, 1, 1), (16, 1, 1), (16, 2, 2)]
