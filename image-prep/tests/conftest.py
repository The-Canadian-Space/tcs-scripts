"""Shared fixtures.

Signing is OFF for every test unless it asks for `signing_enabled`: the
default cert/key paths point at the container's mount and don't exist on a
dev box, and `app_module.provenance.enabled()` is what the endpoint checks.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_APP_DIR = _HERE.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

os.environ.setdefault(
    "TCS_HEADER_FONT",
    str(_APP_DIR / "fonts" / "SpaceGrotesk-VariableFont_wght.ttf"),
)

import app as app_module  # noqa: E402
import provenance  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Redirect the output root to a tmp dir so tests don't touch /output.
    monkeypatch.setattr(app_module, "HEADER_OUTPUT_ROOT", str(tmp_path))
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(scope="session")
def test_signing_material(tmp_path_factory) -> tuple[pathlib.Path, pathlib.Path]:
    """Throwaway ES256 cert + key with the same profile certs/make-cert.sh
    produces (CA:FALSE, digitalSignature, EKU claimSigning+emailProtection,
    SKI, AKI). Generated per session, never written anywhere but pytest's tmp dir."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "The Canadian Space"),
        x509.NameAttribute(NameOID.COMMON_NAME, "TCS image-prep TEST signer"),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        # c2pa-kp-claimSigning (C2PA 2.4) + emailProtection (pre-2.4 validators), as make-cert.sh does.
        .add_extension(x509.ExtendedKeyUsage([
            x509.ObjectIdentifier("1.3.6.1.4.1.62558.2.1"), ExtendedKeyUsageOID.EMAIL_PROTECTION,
        ]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        # c2pa-rs rejects a cert without an Authority Key Identifier
        # ("the certificate is invalid"); openssl adds one implicitly.
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    out = tmp_path_factory.mktemp("c2pa-test-material")
    key_path = out / "test.key.pem"
    cert_path = out / "test.crt.pem"
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,  # c2pa-rs rejects SEC1 "EC PRIVATE KEY"
        serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


@pytest.fixture()
def signing_enabled(monkeypatch, test_signing_material):
    cert_path, key_path = test_signing_material
    monkeypatch.setattr(provenance, "CERT_PATH", str(cert_path))
    monkeypatch.setattr(provenance, "KEY_PATH", str(key_path))
    provenance.reset()
    yield
    provenance.reset()


@pytest.fixture(autouse=True)
def _signing_disabled_by_default(monkeypatch, request):
    """Point at paths that don't exist so provenance.enabled() is False unless
    the test opted in via `signing_enabled` (which overrides these)."""
    if "signing_enabled" in request.fixturenames:
        yield
        return
    monkeypatch.setattr(provenance, "CERT_PATH", "/nonexistent/test.crt.pem")
    monkeypatch.setattr(provenance, "KEY_PATH", "/nonexistent/test.key.pem")
    provenance.reset()
    yield
    provenance.reset()
