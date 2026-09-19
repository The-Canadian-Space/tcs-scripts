"""Happy-path smoke tests for /header.

Network fetch is monkeypatched so the tests run offline and deterministically.
sys.path / font env / `client` fixture come from conftest.py.
"""
from __future__ import annotations

import hashlib
import io

from PIL import Image

import app as app_module


def _synthetic_source(width: int, height: int, color=(30, 60, 120)) -> Image.Image:
    return Image.new("RGB", (width, height), color)


def _install_fake_fetch(monkeypatch, img: Image.Image, fmt: str = "JPEG") -> None:
    # The endpoint fetches raw bytes (so the source can be hashed into the C2PA
    # manifest as an ingredient) and decodes them itself.
    buf = io.BytesIO()
    img.save(buf, fmt)
    data = buf.getvalue()

    def _fake(url):
        return data
    monkeypatch.setattr(app_module, "_fetch_source_bytes", _fake)


def test_header_happy_path_16_9(client, tmp_path, monkeypatch):
    _install_fake_fetch(monkeypatch, _synthetic_source(1600, 900))

    resp = client.post("/header", json={
        "source_url": "https://example.invalid/rocket.jpg",
        "stream_title": "The Daily Broadcast",
        "output_dir": "headers/daily-broadcast",
        "post_id": 2404,
    })
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["dimensions"]["width"] == 600
    # 102 (bar) + 5 (line) + 338 (source scaled to 600 wide from 1600x900) = 445
    assert body["dimensions"]["height"] == 445
    assert body["cached"] is False
    # No signing material configured in this suite → unsigned, and says so.
    assert body["signed"] is False
    assert body["output_url"].startswith("https://assets.thecanadian.space/headers/daily-broadcast/2404-")
    assert body["output_url"].endswith(".jpg")

    # File must actually exist on disk
    expected_hash = hashlib.sha1(b"https://example.invalid/rocket.jpg").hexdigest()[:8]
    expected_path = tmp_path / "headers" / "daily-broadcast" / f"2404-{expected_hash}.jpg"
    assert expected_path.exists()
    assert expected_path.stat().st_size == body["bytes"]

    # Verify the written file decodes back to the expected dimensions
    with Image.open(expected_path) as im:
        assert im.size == (600, 445)


def test_header_cache_hit(client, tmp_path, monkeypatch):
    _install_fake_fetch(monkeypatch, _synthetic_source(1600, 900))

    payload = {
        "source_url": "https://example.invalid/rocket.jpg",
        "stream_title": "The SpaceX Report",
        "output_dir": "headers/spacex-report",
        "post_id": 2405,
    }
    first = client.post("/header", json=payload).get_json()
    assert first["cached"] is False

    second = client.post("/header", json=payload).get_json()
    assert second["cached"] is True
    assert second["output_url"] == first["output_url"]
    assert second["dimensions"] == first["dimensions"]


def test_header_force_regenerates(client, tmp_path, monkeypatch):
    _install_fake_fetch(monkeypatch, _synthetic_source(1600, 900))

    payload = {
        "source_url": "https://example.invalid/rocket.jpg",
        "stream_title": "The Daily Broadcast",
        "output_dir": "headers/daily-broadcast",
        "post_id": 2406,
    }
    first = client.post("/header", json=payload).get_json()
    assert first["cached"] is False

    forced = client.post("/header", json={**payload, "force": True}).get_json()
    assert forced["cached"] is False


def test_header_missing_fields(client):
    resp = client.post("/header", json={"stream_title": "orphan"})
    assert resp.status_code == 400
    assert "source_url" in resp.get_json()["error"]


def test_header_rejects_traversal(client, tmp_path, monkeypatch):
    _install_fake_fetch(monkeypatch, _synthetic_source(800, 500))

    resp = client.post("/header", json={
        "source_url": "https://example.invalid/x.jpg",
        "stream_title": "Malicious",
        "output_dir": "../etc",
        "post_id": 1,
    })
    assert resp.status_code == 400
    assert "invalid" in resp.get_json()["error"]


def test_header_rejects_absolute_path(client, monkeypatch):
    _install_fake_fetch(monkeypatch, _synthetic_source(800, 500))

    resp = client.post("/header", json={
        "source_url": "https://example.invalid/x.jpg",
        "stream_title": "Malicious",
        "output_dir": "/etc/passwd",
        "post_id": 1,
    })
    assert resp.status_code == 400


def test_header_portrait_source(client, tmp_path, monkeypatch):
    # Portrait 4:5 source (800x1000 → scaled to 600x750 → 102 + 5 + 750 = 857)
    _install_fake_fetch(monkeypatch, _synthetic_source(800, 1000))

    resp = client.post("/header", json={
        "source_url": "https://example.invalid/portrait.jpg",
        "stream_title": "Weekly Space Digest",
        "output_dir": "headers/weekly-digest",
        "post_id": 3000,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["dimensions"] == {"width": 600, "height": 857}


def test_composite_still_works(client):
    """Regression: /header changes must not break /composite."""
    bg = _synthetic_source(400, 400, color=(10, 10, 30))
    fg = _synthetic_source(100, 100, color=(255, 255, 255))
    bg_buf = io.BytesIO(); bg.save(bg_buf, "PNG"); bg_buf.seek(0)
    fg_buf = io.BytesIO(); fg.save(fg_buf, "PNG"); fg_buf.seek(0)
    resp = client.post(
        "/composite",
        data={
            "background": (bg_buf, "bg.png"),
            "foreground": (fg_buf, "fg.png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
