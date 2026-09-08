#!/usr/bin/env python3
"""
ovh-wrapper — CLI over the OVH Canada VPS API.

Handles HMAC-SHA1 request signing via the `ovh` PyPI package (n8n can't
sign OVH's non-Bearer auth natively). Invoked by `maintenance.sh` for
snapshot lifecycle (tcs-docs#113) and by n8n workflows via the SSH
utility for billing ingest (#114) + IP inventory (#115).

Auth: reads OVH_ENDPOINT + OVH_APPLICATION_KEY + OVH_APPLICATION_SECRET
+ OVH_CONSUMER_KEY from /opt/tcs/ovh-wrapper/.env (600 perms).

Region defaults to ca.api.ovh.com (our account is Canadian —
critical, eu.api.ovh.com will reject).

VPS name defaults to vps-934e59a6.vps.ovh.net; override via
OVH_VPS_NAME env var.

Exit codes:
    0 — success
    1 — OVH API error (auth, permission, malformed request, etc.)
    2 — config error (missing creds, missing deps)
    3 — timeout (poll-task exceeded --max-wait)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ─── .env loader (falls back to manual parse if python-dotenv missing) ─
def _load_env():
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
        return
    except ImportError:
        pass
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


try:
    import ovh
    from ovh.exceptions import APIError
except ImportError:
    sys.stderr.write(
        "Missing dependency: `ovh` package.\n"
        "Install in the venv:\n"
        "  cd /opt/tcs/ovh-wrapper && source venv/bin/activate && pip install -r requirements.txt\n"
    )
    sys.exit(2)


# ─── Config ─────────────────────────────────────────────────────────
ENDPOINT = os.environ.get("OVH_ENDPOINT", "ovh-ca")
APP_KEY = os.environ.get("OVH_APPLICATION_KEY", "")
APP_SECRET = os.environ.get("OVH_APPLICATION_SECRET", "")
CONSUMER_KEY = os.environ.get("OVH_CONSUMER_KEY", "")
VPS_NAME = os.environ.get("OVH_VPS_NAME", "vps-934e59a6.vps.ovh.net")


def _client():
    if not (APP_KEY and APP_SECRET and CONSUMER_KEY):
        sys.stderr.write(
            "Missing OVH credentials.\n"
            "Set OVH_APPLICATION_KEY, OVH_APPLICATION_SECRET, OVH_CONSUMER_KEY "
            "in /opt/tcs/ovh-wrapper/.env (600 perms, ubuntu-owned).\n"
            "Retrieve from 1Password entry `OVH_API_TCS` or create fresh at "
            "https://ca.api.ovh.com/createToken/ (see docs/infrastructure/ovh-api.md).\n"
        )
        sys.exit(2)
    return ovh.Client(
        endpoint=ENDPOINT,
        application_key=APP_KEY,
        application_secret=APP_SECRET,
        consumer_key=CONSUMER_KEY,
    )


# ─── Commands ───────────────────────────────────────────────────────
def cmd_me(_args):
    """GET /me — validates auth + returns customer info. Safe smoke test."""
    return _client().get("/me")


def cmd_create_snapshot(args):
    """POST /vps/{name}/createSnapshot. Returns task JSON with id to poll."""
    desc = args.description or f"pre-maintenance-{datetime.now().strftime('%Y%m%d')}"
    return _client().post(f"/vps/{VPS_NAME}/createSnapshot", description=desc)


def cmd_poll_task(args):
    """
    GET /vps/{name}/task/{taskId} until state in (done, error, cancelled).
    Exponential backoff: 2, 5, 10, 30, 30… seconds. Raises TimeoutError
    after --max-wait seconds (default 900 = 15 min).
    """
    delays = [2, 5, 10, 30]
    delay_idx = 0
    started = time.time()
    c = _client()
    while True:
        task = c.get(f"/vps/{VPS_NAME}/tasks/{args.task_id}")
        state = task.get("state", "unknown")
        if state in ("done", "error", "cancelled"):
            return task
        if time.time() - started > args.max_wait:
            raise TimeoutError(
                f"Task {args.task_id} still {state!r} after {args.max_wait}s"
            )
        wait = delays[delay_idx] if delay_idx < len(delays) else 30
        delay_idx += 1
        time.sleep(wait)


def cmd_list_snapshots(_args):
    """
    GET /vps/{name}/snapshot — returns the CURRENT snapshot object
    (single, not a list — VPS-2 tier has 1 slot). Raises 404 via
    `ResourceNotFoundError` when no snapshot exists.

    Kept the name `list-snapshots` for CLI stability; the response
    is a single dict, not an array.
    """
    return _client().get(f"/vps/{VPS_NAME}/snapshot")


def cmd_restore_snapshot(_args):
    """
    POST /vps/{name}/snapshot/revert. DESTRUCTIVE-REVERSIBLE. No id
    argument — the endpoint operates on the current (only) snapshot.
    Returns task JSON; poll it with poll-task to know when the restore
    completes. Auto-deletes the snapshot after successful restore.
    """
    return _client().post(f"/vps/{VPS_NAME}/snapshot/revert")


def cmd_delete_snapshot(_args):
    """
    DELETE /vps/{name}/snapshot. No id argument — the endpoint operates
    on the current (only) snapshot. Returns task JSON; poll to confirm
    deletion. Called by maintenance.sh after a successful run to free
    the VPS-2 slot for the next run.
    """
    return _client().delete(f"/vps/{VPS_NAME}/snapshot")


def cmd_get_latest_bill(_args):
    """
    GET /me/bill filtered to last 30 days, then GET /me/bill/{id} for
    each returned id (OVH returns just ids from the list endpoint).
    Returns list of bill summary dicts.
    """
    c = _client()
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    # OVH filter syntax: date.from (period in query key name)
    ids = c.get("/me/bill", **{"date.from": since})
    return [c.get(f"/me/bill/{bid}") for bid in ids]


def cmd_get_bill_details(args):
    """
    GET /me/bill/{billId}/details — returns list of detail ids.
    Then GET /me/bill/{billId}/details/{detailId} for each. Returns
    list of full line-item dicts.
    """
    c = _client()
    detail_ids = c.get(f"/me/bill/{args.bill_id}/details")
    return [
        c.get(f"/me/bill/{args.bill_id}/details/{did}") for did in detail_ids
    ]


def cmd_list_ips(_args):
    """GET /ip — list all IPs / IP ranges on the account."""
    return _client().get("/ip")


def cmd_get_ip_details(args):
    """
    GET /ip/{ip}/reverse — returns list of PTR record dicts for the IP.
    Empty list = no reverse DNS set.
    """
    return _client().get(f"/ip/{args.ip}/reverse")


# ─── argparse ───────────────────────────────────────────────────────
def _build_parser():
    p = argparse.ArgumentParser(
        prog="ovh-wrapper",
        description="OVH Canada VPS API CLI — snapshot / billing / IP ops.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("me", help="Validate auth via GET /me")

    p_snap = sub.add_parser("create-snapshot", help="Create a VPS snapshot")
    p_snap.add_argument(
        "--description",
        default=None,
        help="Snapshot description (default: pre-maintenance-YYYYMMDD)",
    )

    p_poll = sub.add_parser("poll-task", help="Poll a VPS task until done/error")
    p_poll.add_argument("task_id", type=int)
    p_poll.add_argument(
        "--max-wait",
        type=int,
        default=900,
        help="Max wait in seconds (default 900 = 15 min)",
    )

    sub.add_parser(
        "list-snapshots",
        help="Get the current VPS snapshot (single object, or 404 if none)",
    )

    sub.add_parser(
        "restore-snapshot",
        help="Restore the current snapshot (DESTRUCTIVE-REVERSIBLE, no id needed)",
    )

    sub.add_parser(
        "delete-snapshot",
        help="Delete the current snapshot (frees the VPS-2 slot, no id needed)",
    )

    sub.add_parser("get-latest-bill", help="Bills from the last 30 days")

    p_bd = sub.add_parser("get-bill-details", help="Line-item details for a bill")
    p_bd.add_argument("bill_id")

    sub.add_parser("list-ips", help="List all account IPs / IP ranges")

    p_ip = sub.add_parser("get-ip-details", help="Reverse DNS for an IP")
    p_ip.add_argument("ip")

    return p


COMMANDS = {
    "me": cmd_me,
    "create-snapshot": cmd_create_snapshot,
    "poll-task": cmd_poll_task,
    "list-snapshots": cmd_list_snapshots,
    "restore-snapshot": cmd_restore_snapshot,
    "delete-snapshot": cmd_delete_snapshot,
    "get-latest-bill": cmd_get_latest_bill,
    "get-bill-details": cmd_get_bill_details,
    "list-ips": cmd_list_ips,
    "get-ip-details": cmd_get_ip_details,
}


def main():
    args = _build_parser().parse_args()
    fn = COMMANDS[args.cmd]
    try:
        out = fn(args)
    except APIError as e:
        sys.stderr.write(f"OVH API error: {e}\n")
        sys.exit(1)
    except TimeoutError as e:
        sys.stderr.write(f"Timeout: {e}\n")
        sys.exit(3)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
