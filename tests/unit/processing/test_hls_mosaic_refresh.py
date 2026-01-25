from planetarble.processing.manager import _refresh_hls_scene_urls
from planetarble.core.models import HLSConfig


def test_refresh_hls_scene_urls_strips_query(monkeypatch) -> None:
    def fake_token(collection, timeout):  # type: ignore[no-untyped-def]
        return "token=abc"

    def fake_append(url, token):  # type: ignore[no-untyped-def]
        return f"{url}?{token}"

    monkeypatch.setattr("planetarble.processing.manager.fetch_sas_token", fake_token)
    monkeypatch.setattr("planetarble.processing.manager.append_sas_token", fake_append)

    scenes = [
        {
            "collection_id": "hls2-l30",
            "bands": {
                "B04": "https://example.com/data/B04.tif?old=token",
                "B03": "https://example.com/data/B03.tif?old=token",
            },
            "qa_asset": "https://example.com/data/Fmask.tif?old=token",
        }
    ]
    refreshed = _refresh_hls_scene_urls(scenes, HLSConfig())
    bands = refreshed[0]["bands"]
    assert bands["B04"].endswith("?token=abc")
    assert "old=token" not in bands["B04"]
