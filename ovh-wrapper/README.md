# ovh-wrapper

Python CLI over the OVH Canada VPS API. Handles HMAC-SHA1 request signing (n8n can't do this natively) via the `ovh` PyPI package.

Called by:
- `/opt/tcs/scripts/maintenance.sh` — snapshot lifecycle (create pre-run, restore on failure). Wires [tcs-docs#113](https://github.com/The-Canadian-Space/tcs-docs/issues/113).
- n8n `Tool - SSH Utility` — billing ingest cron ([tcs-docs#114](https://github.com/The-Canadian-Space/tcs-docs/issues/114)) + IP inventory cron ([tcs-docs#115](https://github.com/The-Canadian-Space/tcs-docs/issues/115)).

Full capability catalog + endpoint decisions: [`tcs-docs/docs/infrastructure/ovh-api.md`](https://docs.thecanadian.space/infrastructure/ovh-api/).

## Deploy (first time on a VPS)

Assumes ubuntu user on the OVH VPS, Python 3.10+, `jq` installed.

```bash
sudo mkdir -p /opt/tcs/ovh-wrapper
sudo chown -R ubuntu:ubuntu /opt/tcs/ovh-wrapper
cd /opt/tcs/ovh-wrapper

# Copy source (from tcs-scripts checkout or scp)
# — wrapper.py, requirements.txt, .env.example, install.sh, README.md

bash install.sh
```

`install.sh` creates the venv, installs `ovh` + `python-dotenv`, copies `.env.example` → `.env` (only if `.env` doesn't already exist), sets 600 perms on `.env`.

## Configure

Edit `/opt/tcs/ovh-wrapper/.env` with the OVH Canada triplet:

```
OVH_ENDPOINT=ovh-ca
OVH_APPLICATION_KEY=<from 1Password: OVH_API_TCS>
OVH_APPLICATION_SECRET=<from 1Password>
OVH_CONSUMER_KEY=<from 1Password>
```

Verify: `chmod 600 .env` (must be ubuntu-owned + only-user-readable).

## Verify auth

```bash
cd /opt/tcs/ovh-wrapper
source venv/bin/activate
python3 wrapper.py me
```

Success = JSON dump of the OVH customer info (email, phone, address, etc.). Any 401/403 = credential issue; re-check the triplet.

## Subcommands

| Subcommand | Purpose | Blast radius |
|---|---|---|
| `me` | Validate auth (`GET /me`) | read-only |
| `create-snapshot [--description ...]` | Create VPS snapshot; returns task JSON with `id` | reversible-write |
| `poll-task <taskId> [--max-wait 900]` | Poll VPS task until `done` / `error` / `cancelled` | read-only |
| `list-snapshots` | List current snapshots | read-only |
| `restore-snapshot <snapshotId>` | Roll back to snapshot; returns task JSON | **destructive-reversible** |
| `get-latest-bill` | Bills from last 30 days (fetches list ids, then summary per bill) | read-only |
| `get-bill-details <billId>` | Line-item details for one bill | read-only |
| `list-ips` | All account IPs + ranges | read-only |
| `get-ip-details <ip>` | Reverse DNS (PTR) for an IP | read-only |

All commands print JSON on stdout. Errors go to stderr with non-zero exit.

**Exit codes:** `0` success · `1` OVH API error · `2` config error (missing creds/deps) · `3` timeout (poll exceeded `--max-wait`).

## Region

Hardcoded to `ovh-ca` (`ca.api.ovh.com`) via `OVH_ENDPOINT`. Our OVH account is Canadian; `eu.api.ovh.com` will reject with 401 even with valid credentials.

## VPS name

Defaults to `vps-934e59a6.vps.ovh.net` (current TCS VPS). Override via `OVH_VPS_NAME` env var. When the VPS is replaced/renamed, update the `.env`.

## Related

- OVH API capability catalog: [`docs/infrastructure/ovh-api.md`](https://docs.thecanadian.space/infrastructure/ovh-api/)
- Credential storage: [`docs/infrastructure/credentials-and-secrets.md` § `OVH_API`](https://docs.thecanadian.space/infrastructure/credentials-and-secrets/)
- Maintenance script (wire target): `/opt/tcs/scripts/maintenance.sh`
- Snapshot ticket: [tcs-docs#113](https://github.com/The-Canadian-Space/tcs-docs/issues/113)
- Billing ingest ticket: [tcs-docs#114](https://github.com/The-Canadian-Space/tcs-docs/issues/114)
- IP inventory ticket: [tcs-docs#115](https://github.com/The-Canadian-Space/tcs-docs/issues/115)
