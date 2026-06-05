"""Tests for sun-angle-diverse HLS scene selection (fix E).

A deep median needs scenes spread across the season: building shadows shift
with sun azimuth/elevation, so a stack clustered in a few autumn weeks keeps the
same shadows in every scene and the median cannot remove them. select_scene_stack
bins candidates by acquisition date and keeps the lowest-cloud scene per bin.
"""

from __future__ import annotations

from datetime import date

from planetarble.acquisition.hls import HLSScene, select_scene_stack


def _scene(day: str, cloud: float, sid: str = "") -> HLSScene:
    return HLSScene(
        collection_id="hls2-s30",
        item_id=sid or f"{day}_{cloud}",
        acquisition_date=date.fromisoformat(day),
        cloud_cover=cloud,
        bbox=(0, 0, 1, 1),
        bands={"B02": "u", "B03": "u", "B04": "u"},
        qa_asset="qa",
    )


def test_returns_all_when_not_enough() -> None:
    scenes = [_scene("2024-05-01", 5), _scene("2024-06-01", 10)]
    assert select_scene_stack(scenes, 4) == scenes


def test_spreads_across_season() -> None:
    # 8 scenes clustered in autumn + a spring and a summer one
    scenes = [
        _scene("2024-04-10", 30, "spring"),
        _scene("2024-07-15", 25, "summer"),
        _scene("2024-09-20", 12, "a1"),
        _scene("2024-09-25", 8, "a2"),
        _scene("2024-10-01", 15, "a3"),
        _scene("2024-10-05", 9, "a4"),
        _scene("2024-10-10", 11, "a5"),
        _scene("2024-10-15", 7, "a6"),
    ]
    picked = select_scene_stack(scenes, 4)
    assert len(picked) == 4
    dates = sorted(s.acquisition_date for s in picked)
    # the stack spans the season (a non-autumn scene survives) instead of
    # clustering in autumn like the old lowest-cloud-only behaviour
    assert dates[0] <= date(2024, 7, 31)
    assert dates[-1] >= date(2024, 10, 1)


def test_lowest_cloud_within_bin() -> None:
    # two tight clusters; lowest-cloud per temporal bin is chosen
    scenes = [
        _scene("2024-05-01", 20, "may_hi"),
        _scene("2024-05-03", 5, "may_lo"),
        _scene("2024-09-01", 18, "sep_hi"),
        _scene("2024-09-03", 4, "sep_lo"),
    ]
    picked = select_scene_stack(scenes, 2)
    ids = {s.item_id for s in picked}
    assert ids == {"may_lo", "sep_lo"}
