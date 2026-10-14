# AGENTS.md — tcs-scripts

**Per-workflow JavaScript for n8n Code nodes, plus the VPS-side Python: the `image-prep/` service and the scripts n8n runs over SSH.**

> **First-time context:** start with the top-level [AGENTS.md](../AGENTS.md) in the working directory. This file is repo-specific.

## What lives here

Each subdirectory corresponds to one V3 blog pipeline (or a shared service). The JavaScript is copied into those workflows' Code nodes, which are all JavaScript: the n8n image has no Python runner (281 Code nodes checked 2026-10-13). The Python runs on the VPS: `image-prep/` as a container, `write_csa_articles.py` and `write_mls_articles.py` from `/opt/tcs/scripts/` through n8n SSH nodes, and `ovh-wrapper/` from the Sunday maintenance script.

| Directory | Purpose |
|---|---|
| `canada-from-orbit/` | CFO-specific article writers, feed helpers |
| `the-daily-broadcast/` | DB V3 scrapers + writers |
| `the-spacex-report/` | SpaceX-specific article scraping + writing |
| `the-commercial-space/` | Commercial Space source-specific helpers (Firefly, Stoked, Relativity, Axiom) |
| `the-nasa-overview/` | NASA-specific writers |
| `rocketlab-roundup/` | Rocket Lab article writers |
| `social-posts/` | Social media utilities (Instagram carousel, X post prep) |
| `llm-usage-tracker/` | Cost aggregation helpers |
| **`image-prep/`** | **Flask image composition service. LIVE. See below.** |
| `ovh-wrapper/` | OVH API wrapper for the Sunday maintenance snapshots; deployed to `/opt/tcs/ovh-wrapper/` |
| `wp-snippets/` | WordPress PHP snippets (header image) |
| `write_csa_articles.py`, `write_mls_articles.py` | Direct dependencies of Canada From Orbit V3, run by its SSH nodes as `python3 /opt/tcs/scripts/write_*_articles.py` |

## image-prep — special notes

`image-prep/` is the source for a **live** Docker container (`tcs-image-prep`) running on OVH at `/opt/tcs/image-prep/`. Called by the Social Posts workflow via `http://image-prep:3001` on the compose network.

**Currently the VPS copy is a manual clone dated 2026-05-19, not auto-synced with this repo.** If you edit files here, you also need to sync to the VPS:

```bash
# Option A: rsync from local
rsync -av tcs-scripts/image-prep/ ubuntu@vps.thecanadian.space:/opt/tcs/image-prep/

# Option B (better long-term): git clone this subdir onto the VPS at /opt/tcs/image-prep/
```

After sync, rebuild + restart:
```bash
ssh -i C:/Users/CHRISC~1/.ssh/ovh_vps ubuntu@vps.thecanadian.space
cd /opt/tcs/n8n
sudo docker compose build image-prep
sudo docker compose up -d image-prep
```

## Working principles

TCS-wide practices (test-before-deploy, docs-in-same-PR) live in the parent — [`../AGENTS.md`](../AGENTS.md). Repo-specific below:

1. **Read the caller before editing.** If you're modifying a script, find which workflow calls it (`grep -r <script_name>` on the tcs-workflows JSON backups) and understand the context.
2. **Preserve function signatures** if callers depend on them. Adding new args with defaults is safe; renaming positional args is not.
3. **Deploy path awareness.** JavaScript runs inside n8n Code nodes, where only `gm` and `fs` can be required. Python runs on the VPS, SSH'd from n8n or as the `image-prep` container. Which one matters for imports + filesystem paths.
4. **Corresponding workflow doc lives at `tcs-docs/docs/workflows/<workflow>.md`** — update it in the same PR when script interface changes.

## How to verify (before flagging In QA or closing)

TCS-wide verification checklist (run-locally, cost-check, reversibility) lives in the parent's Six Working Principles — [`../AGENTS.md`](../AGENTS.md). Repo-specific below:

- **n8n code nodes:** dry-run the node in the n8n Editor with sample input. Check for missing `pairedItem`, wrong data structure, or silent errors that don't propagate.
- **`image-prep/` changes:** after rsync + rebuild, hit `http://image-prep:3001` from the compose network to verify the container serves before the Social Posts workflow tries it.

## Related docs

- [Docs site](https://docs.thecanadian.space/)
- Top-level [AGENTS.md](../AGENTS.md) for env access
