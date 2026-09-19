"""One-shot: attach C2PA manifests to header JPEGs written before tcs-scripts#21.

Run inside the container so it sees the same cert mount and output volume:

    docker compose exec image-prep python /app/backfill_sign.py --dry-run
    docker compose exec image-prep python /app/backfill_sign.py

Walks ``TCS_OUTPUT_ROOT/<subdir>`` for ``*.jpg``, skips anything that already
carries a manifest, and signs the rest in place (sign to a sibling temp,
rename). Backfilled manifests are honest about their limits: the source bytes
are long gone, so there is no ingredient and no ``c2pa.placed`` action, and the
custom header assertion carries a ``note`` saying so.

Exit status is 1 if any file failed to sign, so a cron/one-liner can notice.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

import provenance
from app import HEADER_BAR_HEIGHT, HEADER_LINE_HEIGHT, HEADER_TARGET_WIDTH, SERVICE_VERSION

log = logging.getLogger("tcs-image-prep.backfill")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=os.environ.get("TCS_OUTPUT_ROOT", "/output"),
                    help="output root (default: $TCS_OUTPUT_ROOT or /output)")
    ap.add_argument("--subdir", default="headers",
                    help="subdirectory under root to walk (default: headers)")
    ap.add_argument("--dry-run", action="store_true", help="report what would be signed, change nothing")
    args = ap.parse_args(argv)

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    if not provenance.enabled():
        log.error("signing material not found at %s / %s — nothing to do",
                  provenance.CERT_PATH, provenance.KEY_PATH)
        return 1

    base = Path(args.root) / args.subdir
    if not base.is_dir():
        log.error("no such directory: %s", base)
        return 1

    note = (f"Signed retroactively by backfill_sign.py on {dt.date.today().isoformat()}; "
            "the source photograph was not retained, so no ingredient is attached.")

    counts = {"signed": 0, "already": 0, "failed": 0}
    for path in sorted(base.rglob("*.jpg")):
        if provenance.has_manifest(path):
            counts["already"] += 1
            continue
        if args.dry_run:
            log.info("would sign %s", path.relative_to(args.root))
            counts["signed"] += 1
            continue
        try:
            provenance.sign_to(
                path, path,
                output_name=path.name, service_version=SERVICE_VERSION,
                target_width=HEADER_TARGET_WIDTH, bar_height=HEADER_BAR_HEIGHT,
                line_height=HEADER_LINE_HEIGHT, note=note,
            )
        except Exception:  # noqa: BLE001 — keep going, report at the end
            log.exception("failed: %s", path)
            counts["failed"] += 1
            continue
        counts["signed"] += 1
        log.info("signed %s (%d bytes)", path.relative_to(args.root), path.stat().st_size)

    verb = "would sign" if args.dry_run else "signed"
    log.info("done: %s=%d already-signed=%d failed=%d", verb, counts["signed"], counts["already"], counts["failed"])
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
