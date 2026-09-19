"""C2PA provenance on /header output (tcs-scripts#21).

Signing is enabled per test via the `signing_enabled` fixture (throwaway cert
minted in conftest). Manifests are read back with the real SDK, so these
tests assert what a validator would actually see.
"""
from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image

import app as app_module
import provenance

COMPOSITE_DST = "http://cv.iptc.org/newscodes/digitalsourcetype/composite"

PAYLOAD = {
    "source_url": "https://example.invalid/photos/rocket%20launch.jpg",
    "stream_title": "The Daily Broadcast",
    "output_dir": "headers/daily-broadcast",
    "post_id": 2404,
}


def _source_bytes(width=1600, height=900, fmt="JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (30, 60, 120)).save(buf, fmt)
    return buf.getvalue()


def _install_fetch(monkeypatch, data: bytes) -> None:
    monkeypatch.setattr(app_module, "_fetch_source_bytes", lambda url: data)


def _output_path(tmp_path, payload=PAYLOAD):
    h = hashlib.sha1(payload["source_url"].encode()).hexdigest()[:8]
    return tmp_path / payload["output_dir"] / f"{payload['post_id']}-{h}.jpg"


def _actions(manifest: dict) -> list[dict]:
    (acts,) = [a for a in manifest["assertions"] if a["label"].startswith("c2pa.actions")]
    return acts["data"]["actions"]


def test_signed_header_manifest_shape(client, tmp_path, monkeypatch, signing_enabled):
    _install_fetch(monkeypatch, _source_bytes())

    resp = client.post("/header", json=PAYLOAD)
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["signed"] is True
    assert body["cached"] is False

    out = _output_path(tmp_path)
    assert out.exists()
    assert out.stat().st_size == body["bytes"]
    # No temp files left behind by the sign-then-rename dance.
    assert sorted(p.name for p in out.parent.iterdir()) == [out.name]

    # Still a valid JPEG with the locked dimensions — signing is metadata-only.
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert im.size == (600, 445)

    m = provenance.read_manifest(out)
    assert m is not None, "no manifest read back"
    assert m["claim_generator_info"][0]["name"] == "TCS image-prep"
    assert m["claim_generator_info"][0]["version"] == app_module.SERVICE_VERSION
    assert m["title"] == out.name

    labels = [a["label"] for a in m["assertions"]]
    # The whole point of the re-scope: no AI disclosure, because no AI.
    assert not any("ai-disclosure" in label for label in labels), labels
    assert "c2pa.actions.v2" in labels
    assert provenance.HEADER_ASSERTION_LABEL in labels

    actions = _actions(m)
    by_name = {a["action"]: a for a in actions}
    assert actions[0]["action"] == "c2pa.created"
    assert actions[0]["digitalSourceType"] == COMPOSITE_DST
    assert {"c2pa.placed", "c2pa.resized", "c2pa.edited"} <= set(by_name)
    # The placed action was resolved by the SDK into a hashed link to the ingredient.
    assert by_name["c2pa.placed"]["parameters"]["ingredients"][0]["url"].endswith("c2pa.ingredient.v3")
    assert by_name["c2pa.resized"]["softwareAgent"]["name"] == "TCS image-prep"

    # Exactly one ingredient: the fetched source, as a component, with its URL.
    assert len(m["ingredients"]) == 1
    ing = m["ingredients"][0]
    assert ing["relationship"] == "componentOf"
    assert ing["format"] == "image/jpeg"
    assert ing["title"] == "rocket launch.jpg"  # percent-decoded basename
    assert ing["informational_URI"] == PAYLOAD["source_url"]
    assert "thumbnail" not in ing  # thumbnails disabled — size discipline

    (hdr,) = [a for a in m["assertions"] if a["label"] == provenance.HEADER_ASSERTION_LABEL]
    assert hdr["data"] == {
        "stream_title": "The Daily Broadcast",
        "source_url": PAYLOAD["source_url"],
    }
    # The caller's "post_id" is n8n's execution id; it must not surface in the
    # public manifest under any label.
    assert "2404" not in json.dumps(m["assertions"])


def test_signed_header_size_overhead_is_bounded(client, tmp_path, monkeypatch, signing_enabled):
    """Guard against thumbnails or other embeds creeping back in: the manifest
    should cost a fixed ~15 KB, not scale with the source image."""
    _install_fetch(monkeypatch, _source_bytes(4000, 2250))
    body = client.post("/header", json=PAYLOAD).get_json()
    assert body["signed"] is True
    out = _output_path(tmp_path)

    # Re-render the same composite unsigned to measure the delta.
    unsigned = io.BytesIO()
    with Image.open(out) as im:
        im.save(unsigned, "JPEG", quality=app_module.JPEG_QUALITY, optimize=True)
    overhead = out.stat().st_size - len(unsigned.getvalue())
    assert 0 < overhead < 24_000, overhead


def test_cache_hit_reports_signed_state(client, tmp_path, monkeypatch, signing_enabled):
    _install_fetch(monkeypatch, _source_bytes())
    first = client.post("/header", json=PAYLOAD).get_json()
    assert first == {**first, "cached": False, "signed": True}

    second = client.post("/header", json=PAYLOAD).get_json()
    assert second["cached"] is True
    assert second["signed"] is True
    assert second["bytes"] == first["bytes"]


def test_unsigned_when_no_material(client, tmp_path, monkeypatch):
    """Default suite state: no cert → header still written, `signed: false`."""
    _install_fetch(monkeypatch, _source_bytes())
    body = client.post("/header", json=PAYLOAD).get_json()
    assert body["signed"] is False
    out = _output_path(tmp_path)
    assert out.exists()
    assert provenance.read_manifest(out) is None

    cached = client.post("/header", json=PAYLOAD).get_json()
    assert cached["cached"] is True
    assert cached["signed"] is False


def test_signing_failure_falls_back_to_unsigned(client, tmp_path, monkeypatch, signing_enabled):
    """A crash inside the SDK must not lose the header or leave temp files."""
    _install_fetch(monkeypatch, _source_bytes())

    def _boom(*args, **kwargs):
        raise provenance.c2pa.C2paError("simulated signer failure")
    monkeypatch.setattr(provenance, "_sign_once", _boom)

    resp = client.post("/header", json=PAYLOAD)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["signed"] is False
    out = _output_path(tmp_path)
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == (600, 445)
    assert provenance.read_manifest(out) is None
    assert sorted(p.name for p in out.parent.iterdir()) == [out.name]


def test_unattachable_source_format_still_signs(client, tmp_path, monkeypatch, signing_enabled):
    """A BMP source can't be a c2pa ingredient; the header is signed without one."""
    _install_fetch(monkeypatch, _source_bytes(fmt="BMP"))
    body = client.post("/header", json=PAYLOAD).get_json()
    assert body["signed"] is True
    m = provenance.read_manifest(_output_path(tmp_path))
    assert m["ingredients"] == [] if "ingredients" in m else True
    assert "c2pa.placed" not in {a["action"] for a in _actions(m)}
    assert {a["action"] for a in _actions(m)} >= {"c2pa.created", "c2pa.resized", "c2pa.edited"}


def test_manifest_cbor_uses_spec_field_name_for_ingredient_uri(tmp_path, signing_enabled):
    """`informational_URI` is the SDK's JSON key; the CBOR must carry the C2PA 2.4
    name `informationalURI`. Pins the SDK's translation so a future rename
    (or a well-meaning "fix" to camelCase, which the SDK silently drops) is caught."""
    src = tmp_path / "u.jpg"
    Image.new("RGB", (600, 445)).save(src, "JPEG")
    manifest_bytes = provenance.sign_to(
        src, tmp_path / "s.jpg", output_name="s.jpg", service_version="0", target_width=600,
        bar_height=102, line_height=5, source_url="https://example.invalid/p.jpg",
        source_bytes=_source_bytes(), source_format="JPEG",
    )
    assert b"informationalURI" in manifest_bytes
    assert b"informational_URI" not in manifest_bytes
    assert b"https://example.invalid/p.jpg" in manifest_bytes


@pytest.mark.parametrize("url", [
    "https://cdn.invalid/img.jpg?api_key=abc123",
    "https://cdn.invalid/img.jpg?w=1200&X-Amz-Signature=deadbeef",
    "https://cdn.invalid/img.jpg?token=x",
    "https://user:pass@cdn.invalid/img.jpg",
    "ftp://cdn.invalid/img.jpg",
    "not a url",
])
def test_credential_shaped_source_url_is_not_embedded(client, tmp_path, monkeypatch, signing_enabled, url):
    _install_fetch(monkeypatch, _source_bytes())
    payload = {**PAYLOAD, "source_url": url}
    body = client.post("/header", json=payload).get_json()
    assert body["signed"] is True  # still signed — just without the URL
    m = provenance.read_manifest(_output_path(tmp_path, payload))
    dumped = json.dumps(m)
    assert "abc123" not in dumped and "deadbeef" not in dumped and "user:pass" not in dumped
    if "://" in url:
        assert url not in dumped  # (the bare-basename ingredient title may legitimately match a non-URL)
    (hdr,) = [a for a in m["assertions"] if a["label"] == provenance.HEADER_ASSERTION_LABEL]
    assert "source_url" not in hdr["data"]
    if m.get("ingredients"):
        assert "informational_URI" not in m["ingredients"][0]


def test_benign_query_string_is_kept():
    """WP CDN size hints (?w=1200, ?fit=…) identify the exact resource; keep them."""
    u = "https://i0.wp.com/site.invalid/img.jpg?w=1200&fit=1200%2C675&ssl=1"
    assert provenance.public_safe_url(u) == u
    assert provenance.public_safe_url("https://x.invalid/a.jpg?signature=1") is None
    assert provenance.public_safe_url("https://x.invalid/a.jpg?sig=1") is None
    assert provenance.public_safe_url("https://x.invalid/a.jpg?design=1") == "https://x.invalid/a.jpg?design=1"
    assert provenance.public_safe_url(None) is None


def test_tsa_failure_falls_back_to_untimestamped_signature(client, tmp_path, monkeypatch, signing_enabled):
    """An unreachable TSA must cost a retry, not the manifest."""
    monkeypatch.setattr(provenance, "TSA_URL", "http://127.0.0.1:9/tsa")  # nothing listens on 9
    _install_fetch(monkeypatch, _source_bytes())
    body = client.post("/header", json=PAYLOAD).get_json()
    assert body["signed"] is True
    m = provenance.read_manifest(_output_path(tmp_path))
    assert m is not None
    assert len(m["ingredients"]) == 1  # ingredient survived; only the timestamp was dropped
    assert not m.get("signature_info", {}).get("time")


def test_source_size_cap(client, monkeypatch):
    class _Resp:
        headers = {"Content-Length": str(app_module.MAX_SOURCE_BYTES + 1)}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return b"\xff" * (app_module.MAX_SOURCE_BYTES + 1)
    monkeypatch.setattr(app_module, "urlopen", lambda req, timeout: _Resp())
    resp = client.post("/header", json=PAYLOAD)
    assert resp.status_code == 400
    assert "limit" in resp.get_json()["error"] or "exceeds" in resp.get_json()["error"]

    # Same without a Content-Length header: the read cap must catch it.
    class _Resp2(_Resp):
        headers = {}
    monkeypatch.setattr(app_module, "urlopen", lambda req, timeout: _Resp2())
    resp = client.post("/header", json=PAYLOAD)
    assert resp.status_code == 400
    assert "exceeds" in resp.get_json()["error"]


@pytest.mark.skipif(provenance.c2pa is None, reason="c2pa SDK not installed")
def test_read_manifest_never_raises_on_bad_files(tmp_path):
    empty = tmp_path / "empty.jpg"; empty.write_bytes(b"")
    garbage = tmp_path / "garbage.jpg"; garbage.write_bytes(b"not a jpeg")
    truncated = tmp_path / "trunc.jpg"
    Image.new("RGB", (200, 200)).save(truncated, "JPEG"); truncated.write_bytes(truncated.read_bytes()[:300])
    missing = tmp_path / "missing.jpg"
    for p in (empty, garbage, truncated, missing):
        assert provenance.read_manifest(p) is None, p.name
        assert provenance.has_manifest(p) is False, p.name


def test_cache_hit_survives_corrupt_cached_file(client, tmp_path, monkeypatch):
    """Cache-hit path must not 500 because the on-disk file has an unreadable manifest."""
    _install_fetch(monkeypatch, _source_bytes())
    client.post("/header", json=PAYLOAD)
    out = _output_path(tmp_path)
    # Keep it a decodable JPEG for Pillow but make sure there's no JUMBF to read.
    with Image.open(out) as im:
        im.load(); im.save(out, "JPEG")
    body = client.post("/header", json=PAYLOAD).get_json()
    assert body["cached"] is True and body["signed"] is False


def test_build_manifest_is_pure_and_documented():
    """The manifest definition is inspectable without the SDK."""
    m = provenance.build_manifest(
        output_name="1-abc.jpg", service_version="9.9.9", target_width=600,
        bar_height=102, line_height=5, stream_title="T",
        source_url="https://x.invalid/a.jpg", ingredient_instance_id="xmp:iid:test",
    )
    assert json.dumps(m)  # serialisable
    acts = m["assertions"][0]["data"]["actions"]
    assert [a["action"] for a in acts] == ["c2pa.placed", "c2pa.resized", "c2pa.edited"]
    assert acts[0]["parameters"] == {"ingredientIds": ["xmp:iid:test"]}
    assert "Non-generative" in acts[2]["description"]
    assert m["assertions"][1]["data"] == {"stream_title": "T", "source_url": "https://x.invalid/a.jpg"}

    # Backfill shape: no ingredient, no placed action, a note instead of source fields.
    b = provenance.build_manifest(
        output_name="1-abc.jpg", service_version="9.9.9", target_width=600,
        bar_height=102, line_height=5, note="backfilled",
    )
    assert [a["action"] for a in b["assertions"][0]["data"]["actions"]] == ["c2pa.resized", "c2pa.edited"]
    assert b["assertions"][1]["data"] == {"note": "backfilled"}


def test_backfill_signs_unsigned_and_is_idempotent(tmp_path, signing_enabled, caplog):
    import backfill_sign

    root = tmp_path / "out"
    d = root / "headers" / "daily-broadcast"
    d.mkdir(parents=True)
    for name in ("2404-a1b2c3d4.jpg", "2405-deadbeef.jpg", "odd-name.jpg"):
        Image.new("RGB", (600, 445), (0, 0, 0)).save(d / name, "JPEG")
    # One that is already signed must be left alone (no double manifest).
    pre = d / "2406-cafebabe.jpg"
    Image.new("RGB", (600, 445)).save(pre, "JPEG")
    provenance.sign_to(pre, pre, output_name=pre.name, service_version="0", target_width=600,
                       bar_height=102, line_height=5)
    pre_size = pre.stat().st_size

    caplog.set_level("INFO")
    assert backfill_sign.main(["--root", str(root), "--dry-run"]) == 0
    assert "would sign=3 already-signed=1 failed=0" in caplog.text
    assert provenance.read_manifest(d / "2404-a1b2c3d4.jpg") is None  # dry-run changed nothing

    assert backfill_sign.main(["--root", str(root)]) == 0
    for name in ("2404-a1b2c3d4.jpg", "2405-deadbeef.jpg", "odd-name.jpg"):
        m = provenance.read_manifest(d / name)
        assert m is not None, name
        (hdr,) = [a for a in m["assertions"] if a["label"] == provenance.HEADER_ASSERTION_LABEL]
        assert set(hdr["data"]) == {"note"}
        assert "retroactively" in hdr["data"]["note"]
        assert "ingredients" not in m or m["ingredients"] == []
        assert "c2pa.placed" not in {a["action"] for a in _actions(m)}
    assert pre.stat().st_size == pre_size  # untouched
    assert not list(d.glob("*.tmp"))

    caplog.clear()
    assert backfill_sign.main(["--root", str(root)]) == 0
    assert "signed=0 already-signed=4 failed=0" in caplog.text


def test_backfill_refuses_without_material(tmp_path):
    import backfill_sign
    assert backfill_sign.main(["--root", str(tmp_path)]) == 1


@pytest.mark.skipif(provenance.c2pa is None, reason="c2pa SDK not installed")
def test_read_manifest_on_plain_jpeg_is_none(tmp_path):
    p = tmp_path / "plain.jpg"
    Image.new("RGB", (10, 10)).save(p, "JPEG")
    assert provenance.read_manifest(p) is None
    assert provenance.has_manifest(p) is False
