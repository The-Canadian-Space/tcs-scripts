"""/fit: make an image fit X's limits (tcs-workflows#293).

X accepted the upload of an 8,250 x 5,490 px NASA image, then refused its media id on the tweet ("Your media IDs are
invalid."). /fit returns an image that already fits byte for byte, and scales anything bigger down to 4,096 px a
side and under 5 MB. `client` comes from conftest.py.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw


def _image(width, height, mode="RGB", color=(30, 90, 160)):
    img = Image.new(mode, (width, height), color)
    draw = ImageDraw.Draw(img)
    for x in range(0, width, max(1, width // 16)):  # some edges, so the encoder has work to do
        draw.line((x, 0, x, height), fill=(240, 200, 40) if mode == "RGB" else None, width=3)
    return img


def _encode(img, fmt, **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def _post(client, data, name="image.jpg", **params):
    return client.post(
        "/fit",
        query_string=params,
        data={"image": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


def _open(resp):
    img = Image.open(io.BytesIO(resp.data))
    img.load()
    return img


def test_an_image_that_fits_comes_back_byte_for_byte(client):
    data = _encode(_image(1200, 674), "JPEG", quality=85)
    resp = _post(client, data)
    assert resp.status_code == 200
    assert resp.data == data
    assert resp.mimetype == "image/jpeg"
    assert resp.headers["X-TCS-Fit"] == "as-is"
    assert (resp.headers["X-TCS-Width"], resp.headers["X-TCS-Height"]) == ("1200", "674")
    assert resp.headers["X-TCS-Bytes"] == str(len(data)) == resp.headers["X-TCS-Source-Bytes"]


@pytest.mark.parametrize("fmt, mime", [("PNG", "image/png"), ("WEBP", "image/webp"), ("GIF", "image/gif")])
def test_other_formats_x_takes_pass_unchanged(client, fmt, mime):
    data = _encode(_image(800, 600), fmt)
    resp = _post(client, data, name=f"image.{fmt.lower()}")
    assert resp.status_code == 200
    assert resp.data == data
    assert resp.mimetype == mime
    assert resp.headers["X-TCS-Fit"] == "as-is"


def test_the_failed_post_s_size_is_scaled_to_4096(client):
    # 2026-11-20, post 2721: NASA's ssc-20261118-s01365.jpg, 8,250 x 5,490 px, 3.8 MB.
    data = _encode(_image(8250, 5490), "JPEG", quality=90)
    resp = _post(client, data)
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.headers["X-TCS-Fit"] == "resized"
    assert (resp.headers["X-TCS-Source-Width"], resp.headers["X-TCS-Source-Height"]) == ("8250", "5490")
    out = _open(resp)
    assert out.format == "JPEG"
    assert out.width == 4096
    assert abs(out.height - round(5490 * 4096 / 8250)) <= 1  # the aspect ratio is kept
    assert (str(out.width), str(out.height)) == (resp.headers["X-TCS-Width"], resp.headers["X-TCS-Height"])
    assert len(resp.data) <= 5_000_000
    assert resp.headers["X-TCS-Bytes"] == str(len(resp.data))


def test_never_scales_up(client):
    data = _encode(_image(300, 200).convert("RGB"), "BMP")  # BMP: not a format X takes, so it is re-encoded
    resp = _post(client, data, name="image.bmp")
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.headers["X-TCS-Fit"] == "re-encoded"
    assert _open(resp).size == (300, 200)


def test_over_the_byte_cap_gets_under_it(client):
    noise = Image.effect_noise((1500, 1000), 90).convert("RGB")  # noise barely compresses
    data = _encode(noise, "JPEG", quality=95)
    assert len(data) > 200_000
    resp = _post(client, data, max_bytes=200_000)
    assert resp.status_code == 200
    assert len(resp.data) <= 200_000
    assert resp.headers["X-TCS-Fit"] in ("resized", "re-encoded")
    assert _open(resp).format == "JPEG"


def test_transparency_becomes_white_not_black(client):
    img = Image.new("RGBA", (5000, 100), (0, 0, 0, 0))  # fully transparent and too wide
    ImageDraw.Draw(img).rectangle((2000, 20, 3000, 80), fill=(200, 0, 0, 255))
    resp = _post(client, _encode(img, "PNG"), name="image.png")
    assert resp.status_code == 200
    out = _open(resp).convert("RGB")
    assert out.width == 4096
    assert min(out.getpixel((5, out.height // 2))) >= 245  # a transparent corner is white


def test_exif_orientation_is_applied_before_it_is_dropped(client):
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 degrees clockwise to display
    data = _encode(_image(600, 300), "JPEG", exif=exif.tobytes())
    resp = _post(client, data, max_side=200)
    assert resp.status_code == 200
    out = _open(resp)
    assert out.size == (100, 200)  # stored 600 x 300 landscape, shown 300 x 600 portrait, fitted into 200
    assert out.getexif().get(0x0112) in (None, 1)


def test_cmyk_jpeg_becomes_rgb(client):
    data = _encode(Image.new("CMYK", (5000, 3000), (0, 128, 255, 0)), "JPEG")
    resp = _post(client, data)
    assert resp.status_code == 200
    assert _open(resp).mode == "RGB"


def test_missing_field(client):
    resp = client.post("/fit", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "image" in resp.get_json()["error"]


def test_not_an_image(client):
    resp = _post(client, b"this is not an image", name="image.jpg")
    assert resp.status_code == 400
    assert "Could not parse image" in resp.get_json()["error"]


@pytest.mark.parametrize("params", [
    {"max_side": "abc"}, {"max_side": 10}, {"max_side": 9000}, {"max_bytes": 1}, {"max_bytes": "5MB"},
])
def test_bad_parameters(client, params):
    resp = _post(client, _encode(_image(100, 100), "JPEG"), **params)
    assert resp.status_code == 400
    assert "must be" in resp.get_json()["error"]


def test_composite_still_answers(client):
    # /fit is new; /composite (Instagram) must be untouched by it.
    bg = _encode(Image.new("RGB", (1080, 1350), (0, 0, 0)), "PNG")
    fg = _encode(_image(1600, 900), "JPEG")
    resp = client.post(
        "/composite",
        data={"background": (io.BytesIO(bg), "bg.png"), "foreground": (io.BytesIO(fg), "fg.jpg")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert _open(resp).size == (1080, 1350)
