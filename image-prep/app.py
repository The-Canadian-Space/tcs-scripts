"""TCS image composition service.

Two endpoints:

- POST /composite — original: two images (background + foreground) → centered
  Instagram-ready composite. See :func:`composite`.
- POST /header — TCS-branded blog header images. Scales a source image to 800px
  wide, adds a 102px black bar on top with the stream title in Space Grotesk
  Bold 44px, and a 5px `#970000` accent line between them. Writes the JPEG to a
  volume-mounted output directory and returns a public URL. Design spec locked
  2026-09-16 in tcs-scripts#5.

Bind: 127.0.0.1:3001 (localhost only — never expose to the public internet).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request, send_file
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

DEFAULT_PADDING_PX = 10
JPEG_QUALITY = 90
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per request

# /header endpoint config
HEADER_FONT_PATH = os.environ.get(
    "TCS_HEADER_FONT",
    "/app/fonts/SpaceGrotesk-VariableFont_wght.ttf",
)
HEADER_OUTPUT_ROOT = os.environ.get("TCS_OUTPUT_ROOT", "/output")
HEADER_ASSET_URL_BASE = os.environ.get(
    "TCS_ASSET_URL_BASE",
    "https://assets.thecanadian.space",
)

# Design spec — locked 2026-09-16 (tcs-scripts#5). Do not change without lock re-approval.
HEADER_TARGET_WIDTH = 800
HEADER_BAR_HEIGHT = 102
HEADER_LINE_HEIGHT = 5
HEADER_LINE_COLOR = (151, 0, 0)  # #970000 — TCS canonical red
HEADER_BAR_COLOR = (0, 0, 0)
HEADER_TEXT_COLOR = (255, 255, 255)
HEADER_FONT_SIZE = 44
HEADER_FONT_VARIATION = "Bold"  # Bold instance of the variable font (weight 700)
HEADER_LETTER_SPACING = 0.5
HEADER_PADDING_H = 24
HEADER_FETCH_TIMEOUT = 30

_SAFE_DIR_RE = re.compile(r"^[a-z0-9][a-z0-9_\-/]*$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

logging.basicConfig(
    level=os.environ.get("TCS_IMAGE_PREP_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tcs-image-prep")


@app.get("/healthz")
def healthz():
    return jsonify(status="ok", service="tcs-image-prep")


@app.post("/composite")
def composite():
    bg_file = request.files.get("background")
    fg_file = request.files.get("foreground")
    if not bg_file or not fg_file:
        return jsonify(error="Both 'background' and 'foreground' file fields are required"), 400

    try:
        bg = Image.open(bg_file.stream)
        bg.load()
        bg = bg.convert("RGBA")
        fg = Image.open(fg_file.stream)
        fg.load()
        fg = fg.convert("RGBA")
    except UnidentifiedImageError as e:
        return jsonify(error=f"Could not parse image: {e}"), 400
    except Exception as e:  # noqa: BLE001 — surface unexpected decode errors to client
        log.exception("Failed to decode input images")
        return jsonify(error=f"Image decode error: {e}"), 400

    try:
        padding = int(request.form.get("padding", DEFAULT_PADDING_PX))
    except (TypeError, ValueError):
        padding = DEFAULT_PADDING_PX
    if padding < 0:
        padding = 0

    bg_w, bg_h = bg.size
    fg_w_in, fg_h_in = fg.size

    # Max box the foreground may occupy, respecting padding on all sides.
    max_w = max(1, bg_w - 2 * padding)
    max_h = max(1, bg_h - 2 * padding)

    # Pillow's thumbnail() preserves aspect AND never enlarges — exactly the
    # behavior we want. If the foreground is already smaller than max_box it
    # is left at its native size.
    fg.thumbnail((max_w, max_h), Image.LANCZOS)
    fg_w, fg_h = fg.size

    # Center the foreground on the background.
    pos_x = (bg_w - fg_w) // 2
    pos_y = (bg_h - fg_h) // 2

    # Paste with alpha mask so any foreground transparency composites cleanly.
    bg.paste(fg, (pos_x, pos_y), fg)

    out = BytesIO()
    bg.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    out.seek(0)

    log.info(
        "composite ok: bg=%dx%d, fg_in=%dx%d, fg_out=%dx%d, pad=%d, pos=(%d,%d)",
        bg_w, bg_h, fg_w_in, fg_h_in, fg_w, fg_h, padding, pos_x, pos_y,
    )

    return send_file(
        out,
        mimetype="image/jpeg",
        as_attachment=False,
        download_name="instagram.jpg",
    )


def _validate_output_dir(path: str) -> None:
    """Sanity-check a caller-supplied relative output directory.

    Belt-and-suspenders for an internal-only service — n8n is the only expected
    caller, but a bad expression there shouldn't be able to write outside the
    configured output root.
    """
    if ".." in path.split("/") or path.startswith("/"):
        raise ValueError(f"invalid output_dir: {path!r}")
    if not _SAFE_DIR_RE.match(path):
        raise ValueError(f"output_dir must match ^[a-z0-9][a-z0-9_\\-/]*$: {path!r}")


def _fetch_source_image(url: str) -> Image.Image:
    """Download a remote image and return it as a Pillow Image (bytes decoded)."""
    req = Request(url, headers={"User-Agent": "TCS-image-prep/1.0"})
    with urlopen(req, timeout=HEADER_FETCH_TIMEOUT) as response:
        data = response.read()
    return Image.open(BytesIO(data))


def _draw_text_with_letter_spacing(draw, xy, text, font, fill, letter_spacing):
    """Pillow has no CSS-style letter-spacing param; we render char-by-char and
    add spacing between characters manually. Matches the 0.5px letter-spacing in
    the design lock."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill, anchor="lm")
        x += font.getlength(char) + letter_spacing


@app.post("/header")
def header():
    payload = request.get_json(silent=True) or {}
    source_url = payload.get("source_url")
    stream_title = payload.get("stream_title")
    output_dir = payload.get("output_dir")
    post_id = payload.get("post_id")
    force = bool(payload.get("force", False))

    # Required-fields check — post_id may be an int, 0 is treated as missing.
    missing = [
        k for k, v in [
            ("source_url", source_url),
            ("stream_title", stream_title),
            ("output_dir", output_dir),
            ("post_id", post_id),
        ]
        if v is None or v == ""
    ]
    if missing:
        return jsonify(error=f"Required fields missing: {', '.join(missing)}"), 400

    try:
        _validate_output_dir(output_dir)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    # Deterministic output filename: {post_id}-{sha1(source_url)[:8]}.jpg
    src_hash = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:8]
    filename = f"{post_id}-{src_hash}.jpg"
    output_root = Path(HEADER_OUTPUT_ROOT)
    output_full_dir = output_root / output_dir
    output_path = output_full_dir / filename
    output_url = f"{HEADER_ASSET_URL_BASE.rstrip('/')}/{output_dir}/{filename}"

    # Cache hit — skip regeneration when the deterministic file already exists
    if output_path.exists() and not force:
        with Image.open(output_path) as cached:
            width, height = cached.size
        log.info("header cache hit: %s (%dx%d)", output_url, width, height)
        return jsonify(
            output_url=output_url,
            dimensions={"width": width, "height": height},
            bytes=output_path.stat().st_size,
            cached=True,
        )

    # Fetch + decode source
    try:
        src = _fetch_source_image(source_url)
        src.load()
        src = src.convert("RGB")
    except (URLError, HTTPError) as e:
        return jsonify(error=f"Could not fetch source image: {e}"), 400
    except UnidentifiedImageError as e:
        return jsonify(error=f"Could not decode source image: {e}"), 400
    except Exception as e:  # noqa: BLE001
        log.exception("Unexpected error fetching/decoding source image")
        return jsonify(error=f"Source image error: {e}"), 400

    # Scale source to 800px wide, aspect-preserved
    src_w_in, src_h_in = src.size
    new_source_h = round(src_h_in * HEADER_TARGET_WIDTH / src_w_in)
    src_scaled = src.resize((HEADER_TARGET_WIDTH, new_source_h), Image.LANCZOS)

    # Build composite: black bar (top) + red accent line + scaled source (bottom)
    total_h = HEADER_BAR_HEIGHT + HEADER_LINE_HEIGHT + new_source_h
    composite_img = Image.new("RGB", (HEADER_TARGET_WIDTH, total_h), HEADER_BAR_COLOR)
    draw = ImageDraw.Draw(composite_img)

    # Red accent line: 5px block between the bar and the source image
    draw.rectangle(
        (0, HEADER_BAR_HEIGHT, HEADER_TARGET_WIDTH, HEADER_BAR_HEIGHT + HEADER_LINE_HEIGHT - 1),
        fill=HEADER_LINE_COLOR,
    )

    # Paste scaled source image below the accent line
    composite_img.paste(src_scaled, (0, HEADER_BAR_HEIGHT + HEADER_LINE_HEIGHT))

    # Font: Space Grotesk variable font, set to Bold (weight 700 via named instance)
    try:
        font = ImageFont.truetype(HEADER_FONT_PATH, HEADER_FONT_SIZE)
    except OSError as e:
        log.exception("Failed to load header font at %s", HEADER_FONT_PATH)
        return jsonify(error=f"Header font unavailable: {e}"), 500
    try:
        font.set_variation_by_name(HEADER_FONT_VARIATION)
    except (OSError, AttributeError):
        # Static font or the named instance isn't there — render at whatever
        # weight the font ships as, and log so we notice.
        log.warning(
            "Font variation %r not applied — rendering at default weight",
            HEADER_FONT_VARIATION,
        )

    # Draw title, left-aligned + vertically centered on the black bar
    text_y = HEADER_BAR_HEIGHT // 2
    _draw_text_with_letter_spacing(
        draw,
        (HEADER_PADDING_H, text_y),
        stream_title,
        font,
        HEADER_TEXT_COLOR,
        HEADER_LETTER_SPACING,
    )

    # Fit-check log (informational — text still renders, gets clipped by the bar)
    if len(stream_title) > 0:
        char_widths = sum(font.getlength(c) for c in stream_title)
        total_text = char_widths + HEADER_LETTER_SPACING * max(0, len(stream_title) - 1)
        inner_w = HEADER_TARGET_WIDTH - 2 * HEADER_PADDING_H
        if total_text > inner_w:
            log.warning(
                "header text overflow: %r needs %.1fpx, inner width is %dpx (clipped)",
                stream_title, total_text, inner_w,
            )

    # Atomic write: temp file → rename, avoids readers seeing a half-written JPEG
    output_full_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".jpg.tmp")
    composite_img.save(tmp_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
    os.replace(tmp_path, output_path)

    file_bytes = output_path.stat().st_size
    log.info(
        "header ok: post_id=%s src=%dx%d out=%dx%d bytes=%d title=%r",
        post_id, src_w_in, src_h_in, HEADER_TARGET_WIDTH, total_h, file_bytes, stream_title,
    )

    return jsonify(
        output_url=output_url,
        dimensions={"width": HEADER_TARGET_WIDTH, "height": total_h},
        bytes=file_bytes,
        cached=False,
    )


if __name__ == "__main__":
    # Dev runner only — production uses gunicorn via systemd.
    # Bind to 127.0.0.1 to keep the service unreachable from the public internet.
    app.run(host="127.0.0.1", port=3001, debug=False)
