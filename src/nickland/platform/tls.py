"""Certificates for the LAN host. PRD §9.6.

A business network has no public name for the host, so no public certificate authority
will issue one. The host therefore becomes its own small authority: it generates a root
certificate once, signs a server certificate for its own names and addresses with it, and
each client trusts that one root, or pins its fingerprint. The root's key never leaves the
host's data directory; the server certificate is reissued from it when the host is renamed
or readdressed, or as it nears expiry, without any client having to be touched.
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

AUTHORITY_DAYS = 3650
SERVER_DAYS = 825
"""The longest validity a mainstream client accepts for a server certificate."""
RENEW_WITHIN_DAYS = 30


@dataclass(frozen=True, slots=True)
class CertificateSet:
    directory: Path

    @property
    def authority(self) -> Path:
        return self.directory / "authority.pem"

    @property
    def authority_key(self) -> Path:
        return self.directory / "authority.key"

    @property
    def certificate(self) -> Path:
        return self.directory / "server.pem"

    @property
    def key(self) -> Path:
        return self.directory / "server.key"

    @property
    def present(self) -> bool:
        return self.certificate.is_file() and self.key.is_file()


@dataclass(frozen=True, slots=True)
class CertificateStatus:
    subject: str
    names: list[str]
    addresses: list[str]
    not_after: datetime
    fingerprint: str
    authority_fingerprint: str | None

    def expires_within(self, days: int, *, now: datetime | None = None) -> bool:
        return self.not_after - (now or datetime.now(UTC)) <= timedelta(days=days)


def fingerprint(certificate: x509.Certificate) -> str:
    """SHA-256 over the DER encoding, as colon-separated hex: what a client pins."""
    digest = certificate.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(digest[index : index + 2] for index in range(0, len(digest), 2))


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    # Windows keeps its own access lists; there the data directory's protection applies.
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def local_names() -> tuple[list[str], list[str]]:
    """The host's own name and addresses, as clients on the network will reach it."""
    names = {"localhost"}
    addresses = {"127.0.0.1"}
    hostname = socket.gethostname()
    if hostname:
        names.add(hostname)
        try:
            names.add(socket.getfqdn(hostname))
            for item in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
                addresses.add(str(item[4][0]))
        except OSError:
            pass
    return sorted(names), sorted(addresses)


def provision(
    directory: Path,
    *,
    business: str,
    names: list[str] | None = None,
    addresses: list[str] | None = None,
    now: datetime | None = None,
) -> CertificateStatus:
    """Create the authority if there is none, and issue a server certificate from it."""
    directory.mkdir(parents=True, exist_ok=True)
    files = CertificateSet(directory)
    moment = now or datetime.now(UTC)
    found_names, found_addresses = local_names()
    names = sorted(set(names or []) | set(found_names))
    addresses = sorted(set(addresses or []) | set(found_addresses))

    if files.authority.is_file() and files.authority_key.is_file():
        authority = x509.load_pem_x509_certificate(files.authority.read_bytes())
        loaded = serialization.load_pem_private_key(files.authority_key.read_bytes(), None)
        assert isinstance(loaded, ec.EllipticCurvePrivateKey)
        authority_key = loaded
    else:
        authority_key = _key()
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, business[:64]),
                x509.NameAttribute(NameOID.COMMON_NAME, f"{business[:40]} local authority"),
            ]
        )
        authority = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(authority_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(moment - timedelta(minutes=5))
            .not_valid_after(moment + timedelta(days=AUTHORITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(authority_key.public_key()),
                critical=False,
            )
            .sign(authority_key, hashes.SHA256())
        )
        _write_private(files.authority_key, _pem(authority_key))
        files.authority.write_bytes(authority.public_bytes(serialization.Encoding.PEM))

    server_key = _key()
    alternatives: list[x509.GeneralName] = [x509.DNSName(name) for name in names]
    alternatives += [x509.IPAddress(ipaddress.ip_address(item)) for item in addresses]
    server = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, business[:64]),
                    x509.NameAttribute(NameOID.COMMON_NAME, names[0] if names else "localhost"),
                ]
            )
        )
        .issuer_name(authority.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(moment - timedelta(minutes=5))
        .not_valid_after(min(moment + timedelta(days=SERVER_DAYS), authority.not_valid_after_utc))
        .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()),
            critical=False,
        )
        .sign(authority_key, hashes.SHA256())
    )
    _write_private(files.key, _pem(server_key))
    files.certificate.write_bytes(
        server.public_bytes(serialization.Encoding.PEM)
        + authority.public_bytes(serialization.Encoding.PEM)
    )
    status = inspect(directory)
    assert status is not None
    return status


def inspect(directory: Path) -> CertificateStatus | None:
    files = CertificateSet(directory)
    if not files.present:
        return None
    certificate = x509.load_pem_x509_certificate(files.certificate.read_bytes())
    try:
        alternatives = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        names = alternatives.get_values_for_type(x509.DNSName)
        addresses = [str(item) for item in alternatives.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        names, addresses = [], []
    authority = (
        fingerprint(x509.load_pem_x509_certificate(files.authority.read_bytes()))
        if files.authority.is_file()
        else None
    )
    return CertificateStatus(
        subject=certificate.subject.rfc4514_string(),
        names=list(names),
        addresses=addresses,
        not_after=certificate.not_valid_after_utc,
        fingerprint=fingerprint(certificate),
        authority_fingerprint=authority,
    )


def server_context(directory: Path) -> ssl.SSLContext:
    """TLS 1.2 or later, for the host's listening socket."""
    files = CertificateSet(directory)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(files.certificate, files.key)
    return context


def peer_fingerprint(host: str, port: int, *, timeout: float = 5.0) -> str:
    """The fingerprint the host presents, read without trusting it, for pinning."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection((host, port), timeout=timeout) as raw,
        context.wrap_socket(raw, server_hostname=host) as wrapped,
    ):
        der = wrapped.getpeercert(binary_form=True)
    assert der is not None
    return fingerprint(x509.load_der_x509_certificate(der))


__all__ = [
    "RENEW_WITHIN_DAYS",
    "CertificateSet",
    "CertificateStatus",
    "fingerprint",
    "inspect",
    "local_names",
    "peer_fingerprint",
    "provision",
    "server_context",
]
