"""Happy-path smoke tests for /header.

Network fetch is monkeypatched so the tests run offline and deterministically.
"""
from __future__ import annotations

import hashlib
import io
import os
import pathlib
import sys

import pytest
from PIL import Image

# Make image-prep/ importable when running from tcs-scripts root or from image-prep/
_HERE = pathlib.Path(__file__).resolve().parent
_APP_DIR = _HERE.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Ensure the endpoint uses a font path that exists at repo layout time.
os.environ.setdefault(
    "TCS_HEADER_FONT",
    str(_APP_DIR / "fonts" / "SpaceGrotesk-VariableFont_wght.ttf"),
)

import app as app_module  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Redirect the output root to a tmp dir so tests don't touch /output.
    monkeypatch.setattr(app_module, "HEADER_OUTPUT_ROOT", str(tmp_path))
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _synthetic_source(width: int, height: int, color=(30, 60, 120)) -> Image.Image:
    return Image.new("RGB", (width, height), color)


def _install_fake_fetch(monkeypatch, img: Image.Image) -> None:
    def _fake(url):
        # Return a fresh Image each call so .load() and .convert() are safe.
        return img.copy()
    monkeypatch.setattr(app_module, "_fetch_source_image", _fake)


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
    assert body["dimensions"]["width"] == 800
    # 102 (bar) + 5 (line) + 450 (source scaled to 800 wide from 1600x900) = 557
    assert body["dimensions"]["height"] == 557
    assert body["cached"] is False
    assert body["output_url"].startswith("https://assets.thecanadian.space/headers/daily-broadcast/2404-")
    assert body["output_url"].endswith(".jpg")

    # File must actually exist on disk
    expected_hash = hashlib.sha1(b"https://example.invalid/rocket.jpg").hexdigest()[:8]
    expected_path = tmp_path / "headers" / "daily-broadcast" / f"2404-{expected_hash}.jpg"
    assert expected_path.exists()
    assert expected_path.stat().st_size == body["bytes"]

    # Verify the written file decodes back to the expected dimensions
    with Image.open(expected_path) as im:
        assert im.size == (800, 557)


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
    # Portrait 4:5 source (800x1000 → scaled to 800x1000 no-op → total 1107)
    _install_fake_fetch(monkeypatch, _synthetic_source(800, 1000))

    resp = client.post("/header", json={
        "source_url": "https://example.invalid/portrait.jpg",
        "stream_title": "Weekly Space Digest",
        "output_dir": "headers/weekly-digest",
        "post_id": 3000,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["dimensions"] == {"width": 800, "height": 1107}


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
