"""C2PA provenance for /header composites (tcs-scripts#21).

Every header JPEG gets a C2PA 2.4 manifest that says, truthfully, what it is:
a *composite* (IPTC digitalSourceType ``composite``) of a source photograph —
attached as a hashed ``componentOf`` ingredient with its URL — placed under a
non-generative title bar rendered by this service. No generative AI is
involved anywhere in ``/header``, so the manifest deliberately carries **no**
``c2pa.ai-disclosure`` assertion (spec §18.28 is an AI *model* disclosure and
says it is not attached when no trained model is invoked) and none of the
"Generative AI" IPTC terms.

Signing material is a self-signed ES256 cert generated on the VPS by
``certs/make-cert.sh`` and mounted read-only into the container. Validators
therefore report the signer as *untrusted* (not on any trust list) while still
verifying the manifest structurally — the intended posture for now.

Policy is fail-open: if the cert is absent or signing throws, the caller keeps
the unsigned JPEG and reports ``signed: false``. The 04:30 UTC blog run must
never block on provenance.
"""
from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import threading
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

try:
    import c2pa
except ImportError:  # pragma: no cover — wheel missing on this platform
    c2pa = None

log = logging.getLogger("tcs-image-prep.provenance")

CLAIM_GENERATOR_NAME = "TCS image-prep"
HEADER_ASSERTION_LABEL = "space.thecanadian.image-prep.header"

# Paths are read from the environment once at import; tests override the module
# attributes and call reset().
CERT_PATH = os.environ.get("TCS_C2PA_CERT", "/app/certs/tcs-image-prep.crt.pem")
KEY_PATH = os.environ.get("TCS_C2PA_KEY", "/app/certs/tcs-image-prep.key.pem")
# Optional RFC 3161 timestamp authority. Off by default: it is a network call on
# the blog hot path (DigiCert answers in ~0.15 s, but a black-holed TSA stalls
# the SDK for ~21 s, measured 2026-09-19, against the caller's 30 s timeout).
# Without a timestamp a manifest validates only while the cert is inside its
# validity window (10 years from minting); with one it validates indefinitely.
# When set, a TSA failure falls back to signing without it — see sign_header.
TSA_URL = os.environ.get("TCS_C2PA_TSA_URL") or None

# Query-string keys that smell like credentials. A source URL carrying one is
# fetched normally but never written into the (public, immutable) manifest.
_CREDENTIAL_QUERY_RE = re.compile(
    r"(?:^|[_-])(?:api_?key|access_?key|secret|token|signature|sig|auth|credential|password|passwd|pwd)(?:$|[_-])",
    re.IGNORECASE,
)

# Pillow format name → MIME type c2pa-rs accepts for an ingredient. Anything
# else is signed without an ingredient rather than failing the whole manifest.
INGREDIENT_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "TIFF": "image/tiff",
}

# Thumbnails off: the SDK would otherwise embed a claim thumbnail and a
# thumbnail of the full-size source, tripling a 600 px header's byte size.
_SDK_SETTINGS = {"builder": {"thumbnail": {"enabled": False}}}

_material: tuple[bytes, bytes] | None = None
_material_lock = threading.Lock()


def reset() -> None:
    """Forget cached signing material (tests swap cert paths between cases)."""
    global _material
    with _material_lock:
        _material = None


def enabled() -> bool:
    """True when the SDK imported and both cert and key are present on disk."""
    return c2pa is not None and os.path.isfile(CERT_PATH) and os.path.isfile(KEY_PATH)


def _load_material() -> tuple[bytes, bytes]:
    global _material
    if _material is None:
        with _material_lock:
            if _material is None:
                _material = (Path(CERT_PATH).read_bytes(), Path(KEY_PATH).read_bytes())
    return _material


def _ingredient_title(source_url: str) -> str:
    name = posixpath.basename(unquote(urlsplit(source_url).path))
    return name or "source-image"


def public_safe_url(url: str | None) -> str | None:
    """``url`` if it is fit to embed in a public manifest, else None.

    Rejects non-http(s) schemes, userinfo (``https://user:pass@host/``) and any
    query parameter whose name looks like a credential. The caller (Blog
    Posting) passes image URLs lifted from already-published article HTML, so
    this should never trigger — it exists because a manifest can't be recalled.
    """
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if "@" in parts.netloc:
        log.warning("provenance: source URL carries userinfo — not embedding it")
        return None
    for key, _ in parse_qsl(parts.query, keep_blank_values=True):
        if _CREDENTIAL_QUERY_RE.search(key):
            log.warning("provenance: source URL query key %r looks like a credential — not embedding the URL", key)
            return None
    return url


def build_manifest(
    *,
    output_name: str,
    service_version: str,
    target_width: int,
    bar_height: int,
    line_height: int,
    stream_title: str | None = None,
    source_url: str | None = None,
    ingredient_instance_id: str | None = None,
    note: str | None = None,
) -> dict:
    """Manifest definition for one header. Pure data — kept separate so tests
    and the backfill script can inspect exactly what will be claimed.

    ``c2pa.created`` (with the composite digitalSourceType) is added by the
    Builder from ``set_intent``; only the follow-on actions are listed here.
    """
    agent = {"name": CLAIM_GENERATOR_NAME, "version": service_version}
    actions = []
    if ingredient_instance_id:
        actions.append({
            "action": "c2pa.placed",
            "softwareAgent": agent,
            "description": "Source photograph placed below the title bar",
            "parameters": {"ingredientIds": [ingredient_instance_id]},
        })
    actions.append({
        "action": "c2pa.resized",
        "softwareAgent": agent,
        "description": f"Source scaled to {target_width} px wide, aspect ratio preserved",
    })
    actions.append({
        "action": "c2pa.edited",
        "softwareAgent": agent,
        "description": (
            f"TCS title bar composited above the source: {bar_height} px black band with the "
            f"stream title in Space Grotesk Bold and a {line_height} px #970000 accent line. "
            "Non-generative — no AI model involved."
        ),
    })

    # Deliberately no post/execution identifier here: the caller's "post_id" is
    # n8n's execution id (the WP post doesn't exist yet when the header is
    # built), and internal ids don't belong in a public manifest.
    header = {k: v for k, v in {
        "stream_title": stream_title,
        "source_url": source_url,
        "note": note,
    }.items() if v is not None}

    return {
        "claim_generator_info": [agent],
        "title": output_name,
        "assertions": [
            {"label": "c2pa.actions", "data": {"actions": actions}},
            {"label": HEADER_ASSERTION_LABEL, "data": header},
        ],
    }


def _sign_once(manifest: dict, ingredient: dict | None, source_bytes: bytes | None,
               source_mime: str | None, src: Path, dst: Path, tsa_url: str | None) -> bytes:
    cert, key = _load_material()
    info = c2pa.C2paSignerInfo(c2pa.C2paSigningAlg.ES256, cert, key, tsa_url)
    with c2pa.Context.from_dict(_SDK_SETTINGS) as ctx, \
            c2pa.Signer.from_info(info) as signer, \
            c2pa.Builder(manifest, ctx) as builder:
        builder.set_intent(c2pa.C2paBuilderIntent.CREATE, c2pa.C2paDigitalSourceType.COMPOSITE)
        if ingredient is not None:
            builder.add_ingredient_from_stream(ingredient, source_mime, BytesIO(source_bytes))
        # Stream form with an explicit MIME: sign_file() infers the format from
        # the extension and our temp files end in .tmp.
        with open(src, "rb") as fin, open(dst, "w+b") as fout:
            return builder.sign(signer, "image/jpeg", fin, fout)


def sign_header(
    src: Path,
    dst: Path,
    *,
    output_name: str,
    service_version: str,
    target_width: int,
    bar_height: int,
    line_height: int,
    stream_title: str | None = None,
    source_url: str | None = None,
    source_bytes: bytes | None = None,
    source_format: str | None = None,
    note: str | None = None,
) -> bytes:
    """Write a signed copy of ``src`` to ``dst``. Returns the manifest bytes.

    ``source_bytes``/``source_format`` (Pillow's ``Image.format``) attach the
    fetched photo as an ingredient. If the SDK rejects the ingredient the
    header is signed again without it rather than left unsigned.
    Raises ``c2pa.C2paError`` (or ``OSError`` for missing material) on failure;
    ``dst`` may then be partial and the caller must discard it.
    """
    if c2pa is None:
        raise RuntimeError("c2pa SDK not importable")

    safe_url = public_safe_url(source_url)
    source_mime = INGREDIENT_MIME.get(source_format or "")
    ingredient = None
    instance_id = None
    if source_bytes and source_mime and source_url:
        instance_id = f"xmp:iid:{uuid.uuid4()}"
        ingredient = {
            "title": _ingredient_title(source_url),
            "relationship": "componentOf",
            "instance_id": instance_id,
            "description": "Source photograph as published; fetched by TCS image-prep for header composition",
        }
        # `informational_URI` is the SDK's JSON-API spelling; it writes the spec
        # 2.4 name `informationalURI` into the CBOR. Passing the camelCase name
        # here is silently dropped (verified against c2pa-rs 0.90.19).
        if safe_url:
            ingredient["informational_URI"] = safe_url
    elif source_bytes and source_url:
        log.warning("provenance: source format %r not attachable as ingredient — signing without", source_format)

    common = dict(
        output_name=output_name, service_version=service_version,
        target_width=target_width, bar_height=bar_height, line_height=line_height,
        stream_title=stream_title, source_url=safe_url, note=note,
    )
    # Degrade in the order of what we'd least like to lose: the timestamp
    # first, then the ingredient. Each attempt is milliseconds except a TSA
    # round-trip.
    attempts: list[tuple[bool, str | None]] = [(ingredient is not None, TSA_URL)]
    if TSA_URL:
        attempts.append((ingredient is not None, None))
    if ingredient is not None:
        attempts.append((False, None))
    for i, (with_ingredient, tsa) in enumerate(attempts):
        manifest = build_manifest(ingredient_instance_id=instance_id if with_ingredient else None, **common)
        try:
            if with_ingredient:
                return _sign_once(manifest, ingredient, source_bytes, source_mime, src, dst, tsa)
            return _sign_once(manifest, None, None, None, src, dst, tsa)
        except c2pa.C2paError as e:
            if i == len(attempts) - 1:
                raise
            log.warning("provenance: signing attempt %d/%d failed (%s: %s) — retrying %s",
                        i + 1, len(attempts), type(e).__name__, str(e)[:200],
                        "without timestamp" if tsa else "without ingredient")
    raise AssertionError("unreachable")


def sign_to(src: Path, final: Path, **kwargs) -> bytes:
    """Sign ``src`` and atomically install the result at ``final``.

    Signs into a sibling ``.<token>.c2pa.tmp`` and renames, so ``final`` is
    never a half-written file and two concurrent requests for the same header
    can't trample each other's temp. Cleans the temp on any failure and re-raises.
    """
    tmp_signed = final.with_name(f"{final.name}.{uuid.uuid4().hex[:8]}.c2pa.tmp")
    try:
        manifest = sign_header(src, tmp_signed, **kwargs)
        os.replace(tmp_signed, final)
        return manifest
    finally:
        tmp_signed.unlink(missing_ok=True)


def read_manifest(path: Path) -> dict | None:
    """Active manifest of ``path`` as a dict, or None if it carries none."""
    if c2pa is None:
        return None
    try:
        with open(path, "rb") as f, c2pa.Reader("image/jpeg", f) as reader:
            store = json.loads(reader.json())
    except (c2pa.C2paError, OSError, ValueError):
        # ManifestNotFound for a plain JPEG, NotSupported/Other for empty or
        # corrupt files, OSError if it vanished, ValueError for unparsable JSON.
        # This runs on the cache-hit hot path: "no usable manifest" is the only
        # answer that must never turn into a 500.
        return None
    active = store.get("active_manifest")
    return store.get("manifests", {}).get(active) if active else None


def has_manifest(path: Path) -> bool:
    return read_manifest(path) is not None
