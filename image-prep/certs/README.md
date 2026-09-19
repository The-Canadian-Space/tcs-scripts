# certs/ — C2PA signing material

Home of the self-signed ES256 certificate `/header` uses to sign its C2PA
provenance manifests (tcs-scripts#21). **Only `make-cert.sh` and this README
are tracked.** The `.gitignore` here blocks everything else so the private key
can never be committed by accident.

## First-time setup on the VPS

```bash
# as the ubuntu user (uid 1000 — the container's imageprep user must be able to read the key)
cd /opt/tcs/image-prep/certs
./make-cert.sh
```

Produces `tcs-image-prep.key.pem` (0600) and `tcs-image-prep.crt.pem` (0644),
valid 10 years. The compose file mounts this directory read-only at
`/app/certs`; restart the container after generating:

```bash
cd /opt/tcs/n8n && sudo docker compose up -d image-prep
sudo docker compose logs image-prep | grep -i provenance   # no "disabled" warning → good
```

Then backfill the headers that predate signing:

```bash
sudo docker compose exec image-prep python /app/backfill_sign.py --dry-run
sudo docker compose exec image-prep python /app/backfill_sign.py
```

## Rotation

`make-cert.sh` refuses to overwrite an existing key. To rotate: delete both
files, run it again, restart the container. Manifests signed with the old cert
keep validating structurally (the cert chain is embedded in each manifest), so
nothing breaks — but a validator that pins our public key would need the new one.

## Why self-signed

No public C2PA trust anchor is needed for provenance embedding: validators
verify structural integrity and report the signer as *untrusted* (not on their
trust list). That is the intended posture. Strong-identity anchoring through a
CA is a follow-up if any platform ever cares.
