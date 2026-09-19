# image-prep

Flask-based image service. Two endpoints:

- **`POST /composite`** — the original Instagram compositor (background + foreground → centered composite).
- **`POST /header`** — TCS-branded blog header images (scaled source + black bar + `#970000` accent line + stream title). Design spec locked 2026-09-16 in [tcs-scripts#5](https://github.com/The-Canadian-Space/tcs-scripts/issues/5).

Callers: the **Social Posts** n8n workflow uses `/composite`; the **Blog Posting** workflow uses `/header` (see [tcs-workflows#65](https://github.com/The-Canadian-Space/tcs-workflows/issues/65)).

---

## `POST /composite`

Preserves background dimensions verbatim (no resize, no crop). Preserves foreground aspect ratio. Shrinks the foreground (never enlarges) to fit inside the background with at least 10 px margin on every side. Centers the foreground on both axes. Emits JPEG q90.

Input: `multipart/form-data` with `background` and `foreground` file fields, and an optional `padding` int (default 10).

Output: JPEG binary.

---

## `POST /header`

Takes a source image URL + stream title, builds a TCS-branded blog header, and writes the JPEG to a mounted volume. Returns the public URL for the WordPress `_tcs_header_url` post meta.

### Design spec (locked 2026-09-16, tcs-scripts#5)

| Property | Value |
|---|---|
| Output width | 600px (aspect-preserved from source) |
| Bar height | 102px |
| Bar color | `#000000` |
| Accent line | 5px, `#970000` (TCS red) |
| Font | Space Grotesk 700 (variable font, Bold instance) |
| Font size | 44px |
| Letter-spacing | 0.5px |
| Case | Mixed (as-typed) |
| Alignment | Left, 24px horizontal padding |
| Text color | `#ffffff` |
| Output format | JPEG q90 |

### Request

`POST /header` with `Content-Type: application/json`:

```json
{
  "source_url": "https://example.com/rocket.jpg",
  "stream_title": "The Daily Broadcast",
  "output_dir": "headers/daily-broadcast",
  "post_id": 2404,
  "force": false
}
```

Fields:

- `source_url` (required) — the image to use as the bottom portion of the composite. Fetched with a 30s timeout.
- `stream_title` (required) — the text rendered on the black bar.
- `output_dir` (required) — path relative to `TCS_OUTPUT_ROOT`. Must match `^[a-z0-9][a-z0-9_\-/]*$`. No leading slash, no `..`.
- `post_id` (required) — WordPress post ID; goes into the deterministic output filename.
- `force` (optional, default `false`) — regenerate even if the deterministic output already exists.

### Response

```json
{
  "output_url": "https://assets.thecanadian.space/headers/daily-broadcast/2404-a1b2c3d4.jpg",
  "dimensions": { "width": 600, "height": 445 },
  "bytes": 87421,
  "cached": false,
  "signed": true
}
```

- `output_url` — public URL for the generated header. Composed from `TCS_ASSET_URL_BASE + output_dir + filename`.
- `dimensions.height` — `102 (bar) + 5 (line) + source_scaled_to_600_wide.height`. Example: a 16:9 source yields height `102 + 5 + 338 = 445`.
- `cached` — `true` if the endpoint short-circuited (deterministic file already existed).
- `signed` — `true` if the JPEG carries a C2PA provenance manifest (see [Provenance](#provenance)). `false` means signing material isn't mounted or signing failed; the header is still written. On a cache hit this reflects the file on disk.

Filenames are `{post_id}-{sha1(source_url)[:8]}.jpg` — deterministic, idempotent, cache-friendly.

### Config (env vars)

| Var | Default | Purpose |
|---|---|---|
| `TCS_OUTPUT_ROOT` | `/output` | Filesystem root under which files are written. Should be a bind-mounted volume in production. |
| `TCS_ASSET_URL_BASE` | `https://assets.thecanadian.space` | URL prefix returned in `output_url`. |
| `TCS_HEADER_FONT` | `/app/fonts/SpaceGrotesk-VariableFont_wght.ttf` | Path to the TTF used for the bar text. |
| `TCS_IMAGE_PREP_LOG_LEVEL` | `INFO` | Python logging level. |
| `TCS_C2PA_CERT` | `/app/certs/tcs-image-prep.crt.pem` | C2PA signing certificate (PEM). Signing is disabled if absent. |
| `TCS_C2PA_KEY` | `/app/certs/tcs-image-prep.key.pem` | Matching PKCS#8 private key (PEM). Signing is disabled if absent. |
| `TCS_C2PA_TSA_URL` | *(unset)* | Optional RFC 3161 timestamp authority (e.g. `http://timestamp.digicert.com`). Off by default — see Provenance → Mechanics for the trade-off. A TSA failure falls back to signing without a timestamp. |

### Errors

- `400` — missing required field, invalid `output_dir`, unreachable/undecodable/oversized (> 25 MB) `source_url`.
- `500` — font file missing/unloadable.

Failures during rendering do not leave partial files — the endpoint writes to a `.tmp` and atomically renames on success. Provenance signing failures are **not** errors: the unsigned header is installed, `signed: false` is returned, and the exception is logged.

### Provenance

Every `/header` JPEG is signed with a [C2PA 2.4](https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html) manifest ([tcs-scripts#21](https://github.com/The-Canadian-Space/tcs-scripts/issues/21)) that records, truthfully, what the file is. Read it back with `c2patool <file>` or `python -c "import provenance, json, sys; print(json.dumps(provenance.read_manifest(sys.argv[1]), indent=1))" <file>`.

What the manifest claims:

| Element | Value | Why |
|---|---|---|
| `c2pa.created` action, `digitalSourceType` | `http://cv.iptc.org/newscodes/digitalsourcetype/composite` | IPTC: *"mix or composite of several elements, any of which may or may not be generative AI"*. The plain, accurate term for photo + rendered title bar. |
| Ingredient (`c2pa.ingredient.v3`) | the fetched source image, `relationship: componentOf`, `informationalURI` = `source_url` (spelled `informational_URI` in the SDK's JSON API), title = its filename | Binds the manifest to exactly what was composited. Skipped only if the source is a format the SDK can't ingest (then signed without). |
| `c2pa.placed` action | links to the ingredient | "The source photo was placed into this composite." |
| `c2pa.resized` action | description of the 600 px scale | |
| `c2pa.edited` action | description of the bar/accent/title; states *non-generative* | |
| `space.thecanadian.image-prep.header` (custom assertion) | `stream_title`, `source_url` (fresh) or `note` (backfill) | Which stream the header was made for. Deliberately no `post_id`: the caller's value is n8n's execution id, and internal ids don't belong in a public manifest. |
| `claim_generator_info` | `TCS image-prep` + `SERVICE_VERSION` | |
| `c2pa.hash.data` | added by the SDK | Standard content binding. |

What it deliberately does **not** claim: `c2pa.ai-disclosure`. Spec §18.28 defines that as an AI *model* disclosure (`modelType` is a required ML-framework enum) and says it is not attached when no trained model is invoked — `/header` is Pillow arithmetic, no model. Likewise none of IPTC's "Generative AI" source types (`trainedAlgorithmicMedia`, `compositeSynthetic`) are used. A validator reads the result as "verified composite, no generative AI declared", which is the truth. If a source image is ever itself AI-generated, that belongs on the *source's* provenance, not here.

Mechanics:

- Signing happens between the `.tmp` write and the atomic rename, into a second temp (`.c2pa.tmp`), so a signing failure can never leave a partial header. Fail-open: any exception → unsigned header, `signed: false`, logged.
- Manifest overhead is a fixed ~13 KB (signature + embedded cert chain + assertions). SDK thumbnails are disabled — they would otherwise add a thumbnail of the full-size source and triple the file.
- Self-signed ES256 cert (EKU `c2pa-kp-claimSigning` + `emailProtection`, 10-year validity), generated on the VPS by `certs/make-cert.sh`, mounted read-only. Validators report the signer as *untrusted* (not on a trust list) while verifying the manifest structurally. See `certs/README.md` for setup and rotation.
- **Timestamps.** Without an RFC 3161 timestamp a manifest validates only while the signing cert is inside its validity window, i.e. until the cert minted at deploy time expires (~2036); after that, validators using the current time reject it. With a timestamp it validates indefinitely. `TCS_C2PA_TSA_URL` is **off by default** because it is a network call on the blog hot path: DigiCert answers in ~0.15 s, but a black-holed TSA stalled the SDK for ~21 s in testing, against Blog Posting's 30 s node timeout. If enabled, a TSA failure costs one retry without the timestamp, never the manifest. Rotate the cert (and backfill) before 2036, or turn the TSA on, whichever matters by then.
- Source URLs are only embedded when they are plain `http(s)` with no userinfo and no credential-shaped query key (`api_key`, `token`, `signature`, …). Otherwise the header is still signed, just without the URL — a manifest is public and can't be recalled.
- The source fetch is capped at `MAX_SOURCE_BYTES` (25 MB, same as uploads); over the cap is a `400`.
- `backfill_sign.py` re-signs headers that predate this (no ingredient — the source bytes are gone; the custom assertion carries a `note` saying so). Idempotent: already-signed files are skipped.
- Cache-hit responses report `signed` by reading the manifest off disk, so a header signed by the backfill shows `signed: true` even though the endpoint never signed it.

---

## Deployment

Runs as a Docker container `tcs-image-prep` inside the OVH n8n docker-compose network at `/opt/tcs/n8n/docker-compose.yml`. Bound internally to port 3001 only — no public port exposure, only reachable from other services on the same compose network via `http://image-prep:3001`.

See `docker-compose.snippet.yml` for the service block. The `/output` volume mount is required by `/header` — the host directory `/opt/tcs/assets` must exist and be owned by uid 1000. See the VPS infrastructure ticket ([tcs-docs#193](https://github.com/The-Canadian-Space/tcs-docs/issues/193)) for the full setup.

### Updating the deployed service

1. Edit source files here (`app.py`, `provenance.py`, `Dockerfile`, `requirements.txt`, etc.).
2. Commit + push to `tcs-scripts`.
3. Ship the subdir to the VPS with `git archive`, not `rsync` from a Windows checkout. **Pass `-c core.autocrlf=false`**: archiving the `image-prep` *subtree* makes it the archive root, so the repo-root `.gitattributes` is not consulted and a Windows clone's `core.autocrlf=true` would otherwise export CRLF files (this bit the first deploy on 2026-09-19 — a CRLF `make-cert.sh` is "bad interpreter").
   ```bash
   git -c core.autocrlf=false -c core.eol=lf archive --format=tar HEAD:image-prep | ssh ubuntu@51.195.43.156 'tar -x -C /opt/tcs/image-prep'
   ```
   Sanity-check on the VPS: `tr -cd '' < /opt/tcs/image-prep/certs/make-cert.sh | wc -c` must print `0`.
4. Rebuild + restart from the compose project: `cd /opt/tcs/n8n && sudo docker compose build image-prep && sudo docker compose up -d image-prep`.
5. Check the log: `sudo docker compose logs --since 2m image-prep`. A `provenance signing disabled` warning means the cert mount is missing or unreadable — see below.

**First deploy with provenance signing** (once):

1. Make sure the compose service has the `TCS_C2PA_*` env and the `/opt/tcs/image-prep/certs:/app/certs:ro` mount from `docker-compose.snippet.yml`.
2. Generate the cert **as the `ubuntu` user, not with `sudo`** — the container's `imageprep` user is uid 1000 and must be able to read the 0600 key: `cd /opt/tcs/image-prep/certs && ./make-cert.sh`. (The directory arrives with the `git archive` in step 3 above; if it doesn't exist yet, `mkdir` it as `ubuntu` *before* `docker compose up`, or Docker creates it root-owned.)
3. Restart the container (step 4 above), confirm no `provenance signing disabled` warning.
4. Sign the headers that predate this: `sudo docker compose exec image-prep python /app/backfill_sign.py --dry-run`, then without `--dry-run`. Idempotent; re-running reports `already-signed`.
5. Smoke-test: re-request one existing header with `"force": true` and confirm `"signed": true`; pull the served JPEG and read the manifest back.

**Note:** The VPS copy at `/opt/tcs/image-prep/` is a manual copy, not a git checkout, and is not automatically synced with this repo. Per the monthly-maintenance backup policy, no `.bak` copies of git-tracked files belong there.

---

## Fonts

`fonts/SpaceGrotesk-VariableFont_wght.ttf` — Space Grotesk variable font (300–700 weight axis). Sourced from [google/fonts](https://github.com/google/fonts/tree/main/ofl/spacegrotesk), licensed under the SIL Open Font License 1.1 (`fonts/OFL.txt`). The `/header` endpoint sets the variation to the `Bold` named instance (weight 700).

---

## Testing

```
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/
```

The test suite mocks the network fetch and writes to a tmp directory, so no external calls are made. Provenance tests mint a throwaway signing cert at session start (via `cryptography`); nothing is read from `certs/`, and every test that doesn't opt in runs with signing disabled.

---

## Callers

- **`The Canadian Space - Social Posts`** — Instagram composite generation via `/composite`.
- **`The Canadian Space - Blog Posting`** — branded header images via `/header` (integration pending [tcs-workflows#65](https://github.com/The-Canadian-Space/tcs-workflows/issues/65)).
- **`TEST BED`** — occasional integration testing.
