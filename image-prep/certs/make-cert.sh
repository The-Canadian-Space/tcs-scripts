#!/usr/bin/env bash
# Generate the self-signed ES256 signing certificate image-prep uses for C2PA
# provenance manifests (tcs-scripts#21).
#
# Run this ON THE VPS (or wherever the container's cert volume lives). The
# private key is written next to the cert and must never leave that host —
# this directory is .gitignored for exactly that reason.
#
#   ./make-cert.sh [OUT_DIR]        default OUT_DIR: the directory this script is in
#
# Produces:
#   OUT_DIR/tcs-image-prep.key.pem   EC P-256 private key, mode 0600
#   OUT_DIR/tcs-image-prep.crt.pem   self-signed X.509, 10 years, C2PA signing profile
#
# Refuses to overwrite an existing key — delete it by hand if you really mean
# to rotate (every previously signed header then validates against a cert no
# longer in the container, so plan a backfill).
#
# Profile matches C2PA 2.4 §14.4/14.5 for a signing cert: X.509 v3, keyUsage
# digitalSignature (critical), EKU c2pa-kp-claimSigning + emailProtection
# (the latter for pre-2.4 validators), not a CA, SKI + AKI (c2pa-rs insists).
# Self-signed means validators report the signer as "untrusted" (not on any
# trust list) while still verifying the manifest structurally — that is the
# intended posture for now; see README.md "Provenance".
set -euo pipefail

OUT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
KEY="$OUT_DIR/tcs-image-prep.key.pem"
CRT="$OUT_DIR/tcs-image-prep.crt.pem"
DAYS=3650
SUBJ="/C=CA/O=The Canadian Space/OU=image-prep/CN=TCS image-prep C2PA signer"

if [[ -e "$KEY" ]]; then
    echo "refusing to overwrite existing key: $KEY" >&2
    exit 1
fi
mkdir -p "$OUT_DIR"

# Any failure from here on removes whatever was produced, so a half-run never
# leaves an orphan key behind to trip the overwrite guard on the retry.
CSR="$(mktemp)"; EXT="$(mktemp)"
DONE=0
cleanup() {
    rm -f "$CSR" "$EXT"
    if [[ "$DONE" -ne 1 ]]; then
        rm -f "$KEY" "$CRT"
        echo "make-cert.sh failed — removed partial output" >&2
    fi
}
trap cleanup EXIT

# Key first, with a restrictive umask so it is never world-readable, even briefly.
# genpkey (not `ecparam -genkey`) so the PEM is PKCS#8 "PRIVATE KEY" — c2pa-rs
# rejects the SEC1 "EC PRIVATE KEY" form.
( umask 077 && openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$KEY" )

# CSR → self-signed cert via `x509 -req -extfile`, not `req -x509 -addext`:
# c2pa-rs refuses a cert without an Authority Key Identifier, and in
# `req -x509` mode `authorityKeyIdentifier=keyid` silently resolves to nothing
# (no issuer cert exists yet). The two-step form derives it from the signing key.
cat > "$EXT" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
# c2pa-kp-claimSigning (1.3.6.1.4.1.62558.2.1) is the C2PA 2.4 EKU; emailProtection
# alongside it keeps pre-2.4 validators happy (spec 14.4.1).
extendedKeyUsage=emailProtection,1.3.6.1.4.1.62558.2.1
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid:always
EOF
openssl req -new -sha256 -key "$KEY" -subj "$SUBJ" -out "$CSR"
openssl x509 -req -sha256 -days "$DAYS" -in "$CSR" -signkey "$KEY" -out "$CRT" -extfile "$EXT"
chmod 0644 "$CRT"

# Self-check: every extension c2pa-rs's profile check needs must be present.
TEXT="$(openssl x509 -in "$CRT" -noout -text)"
for needle in "Digital Signature" "E-mail Protection" "1.3.6.1.4.1.62558.2.1" "CA:FALSE" "Subject Key Identifier" "Authority Key Identifier"; do
    if ! grep -q "$needle" <<< "$TEXT"; then
        echo "generated cert is missing '$needle'" >&2
        exit 1
    fi
done

DONE=1
echo "wrote $KEY (0600) and $CRT (0644)"
openssl x509 -in "$CRT" -noout -subject -dates -ext keyUsage,extendedKeyUsage,basicConstraints,subjectKeyIdentifier,authorityKeyIdentifier
