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
  "cached": false
}
```

- `output_url` — public URL for the generated header. Composed from `TCS_ASSET_URL_BASE + output_dir + filename`.
- `dimensions.height` — `102 (bar) + 5 (line) + source_scaled_to_600_wide.height`. Example: a 16:9 source yields height `102 + 5 + 338 = 445`.
- `cached` — `true` if the endpoint short-circuited (deterministic file already existed).

Filenames are `{post_id}-{sha1(source_url)[:8]}.jpg` — deterministic, idempotent, cache-friendly.

### Config (env vars)

| Var | Default | Purpose |
|---|---|---|
| `TCS_OUTPUT_ROOT` | `/output` | Filesystem root under which files are written. Should be a bind-mounted volume in production. |
| `TCS_ASSET_URL_BASE` | `https://assets.thecanadian.space` | URL prefix returned in `output_url`. |
| `TCS_HEADER_FONT` | `/app/fonts/SpaceGrotesk-VariableFont_wght.ttf` | Path to the TTF used for the bar text. |
| `TCS_IMAGE_PREP_LOG_LEVEL` | `INFO` | Python logging level. |

### Errors

- `400` — missing required field, invalid `output_dir`, unreachable/undecodable `source_url`.
- `500` — font file missing/unloadable.

Failures during rendering do not leave partial files — the endpoint writes to a `.tmp` and atomically renames on success.

---

## Deployment

Runs as a Docker container `tcs-image-prep` inside the OVH n8n docker-compose network at `/opt/tcs/n8n/docker-compose.yml`. Bound internally to port 3001 only — no public port exposure, only reachable from other services on the same compose network via `http://image-prep:3001`.

See `docker-compose.snippet.yml` for the service block. The `/output` volume mount is required by `/header` — the host directory `/opt/tcs/assets` must exist and be owned by uid 1000. See the VPS infrastructure ticket ([tcs-docs#193](https://github.com/The-Canadian-Space/tcs-docs/issues/193)) for the full setup.

### Updating the deployed service

1. Edit source files here (`app.py`, `Dockerfile`, `requirements.txt`, etc.).
2. Commit + push to `tcs-scripts`.
3. SSH to OVH VPS, `cd /opt/tcs/image-prep`, `git pull` (if the deployed copy is git-cloned from this repo) OR `rsync` the updated files across.
4. Rebuild + restart: `sudo docker compose build image-prep && sudo docker compose up -d image-prep`.

**Note:** Currently the VPS copy at `/opt/tcs/image-prep/` is a manual clone dated 2026-05-19. Not automatically synced with this repo. If you want git-based deployment, `git clone` this subdir into `/opt/tcs/image-prep/` on the VPS instead of maintaining a parallel copy.

---

## Fonts

`fonts/SpaceGrotesk-VariableFont_wght.ttf` — Space Grotesk variable font (300–700 weight axis). Sourced from [google/fonts](https://github.com/google/fonts/tree/main/ofl/spacegrotesk), licensed under the SIL Open Font License 1.1 (`fonts/OFL.txt`). The `/header` endpoint sets the variation to the `Bold` named instance (weight 700).

---

## Testing

```
pip install -r requirements-dev.txt
pytest tests/
```

The test suite mocks the network fetch and writes to a tmp directory, so no external calls are made.

---

## Callers

- **`The Canadian Space - Social Posts`** — Instagram composite generation via `/composite`.
- **`The Canadian Space - Blog Posting`** — branded header images via `/header` (integration pending [tcs-workflows#65](https://github.com/The-Canadian-Space/tcs-workflows/issues/65)).
- **`TEST BED`** — occasional integration testing.
