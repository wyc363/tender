from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

import httpx

from ..config import PipelineConfig
from ..utils import sha256_file
from .request_adapter import NormalizedReviewRequest


LOG = logging.getLogger("review_pipeline.http.file_url")
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
PDF_MAGIC_SCAN_BYTES = 64
DnsResolver = Callable[[str, int | None], Sequence[str]]
URL_IN_LOG_RE = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
QUERY_VALUE_RE = re.compile(r"([?&][^=\s&]+)=([^&\s\"']+)")


class _SensitiveUrlLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        rendered = URL_IN_LOG_RE.sub(lambda match: redact_url(match.group(0)), rendered)
        rendered = QUERY_VALUE_RE.sub(r"\1=REDACTED", rendered)
        record.msg = rendered
        record.args = ()
        return True


def _install_http_log_filters() -> None:
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, _SensitiveUrlLogFilter) for item in logger.filters):
            logger.addFilter(_SensitiveUrlLogFilter())


@dataclass(frozen=True)
class ResolvedInput:
    local_path: Path
    original_reference: str
    file_name: str | None
    sha256: str | None = None
    size_bytes: int | None = None
    content_type: str | None = None
    final_url: str | None = None


class InputResolutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class InputResolver(Protocol):
    def resolve(self, request: NormalizedReviewRequest) -> ResolvedInput:
        ...


class UnsupportedFileUrlResolver:
    """Explicit opt-out resolver retained for deployments that disable downloads."""

    def resolve(self, request: NormalizedReviewRequest) -> ResolvedInput:
        raise InputResolutionError(
            "FILE_URL_RESOLUTION_NOT_IMPLEMENTED",
            "Remote file_url resolution is disabled",
        )


def redact_url(value: str) -> str:
    """Return a log-safe URL without credentials, query values, or fragments."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    host = parsed.hostname or "<missing-host>"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    query = "REDACTED" if parsed.query else ""
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", parsed.path, query, ""))


def system_dns_resolver(host: str, port: int | None) -> list[str]:
    service_port = port or 443
    return sorted({
        item[4][0]
        for item in socket.getaddrinfo(host, service_port, type=socket.SOCK_STREAM)
    })


class SafeFileUrlResolver:
    """Download one public HTTP(S) PDF into persistent content-addressed staging."""

    def __init__(
        self,
        *,
        staging_root: Path,
        allowed_schemes: Sequence[str] = ("http", "https"),
        allow_private_network: bool = False,
        trusted_private_hosts: Sequence[str] = (),
        trusted_private_cidrs: Sequence[str] = (),
        max_redirects: int = 3,
        max_download_bytes: int = 64 * 1024 * 1024,
        chunk_size_bytes: int = 64 * 1024,
        connect_timeout_seconds: float = 10,
        read_timeout_seconds: float = 60,
        write_timeout_seconds: float = 10,
        pool_timeout_seconds: float = 10,
        dns_resolver: DnsResolver = system_dns_resolver,
        transport: httpx.BaseTransport | None = None,
    ):
        _install_http_log_filters()
        self.staging_root = Path(staging_root).expanduser().resolve()
        self.allowed_schemes = frozenset(item.lower() for item in allowed_schemes)
        self.allow_private_network = bool(allow_private_network)
        self.trusted_private_hosts = frozenset(
            item.strip().rstrip(".").lower() for item in trusted_private_hosts if item.strip()
        )
        try:
            self.trusted_private_cidrs = tuple(
                ipaddress.ip_network(item, strict=False) for item in trusted_private_cidrs
            )
        except ValueError as exc:
            raise ValueError("file_url.trusted_private_cidrs contains an invalid CIDR") from exc
        self.max_redirects = int(max_redirects)
        self.max_download_bytes = int(max_download_bytes)
        self.chunk_size_bytes = int(chunk_size_bytes)
        self.dns_resolver = dns_resolver
        self.transport = transport
        self.timeout = httpx.Timeout(
            connect=float(connect_timeout_seconds),
            read=float(read_timeout_seconds),
            write=float(write_timeout_seconds),
            pool=float(pool_timeout_seconds),
        )
        if not self.allowed_schemes or not self.allowed_schemes <= {"http", "https"}:
            raise ValueError("allowed_schemes must contain only http and/or https")
        if self.max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        if self.max_download_bytes <= 0 or self.chunk_size_bytes <= 0:
            raise ValueError("download and chunk sizes must be positive")

    @classmethod
    def from_config(
        cls,
        config: PipelineConfig,
        *,
        dns_resolver: DnsResolver = system_dns_resolver,
        transport: httpx.BaseTransport | None = None,
    ) -> "SafeFileUrlResolver":
        return cls(
            staging_root=config.effective_staging_root,
            allowed_schemes=config.file_url_allowed_schemes,
            allow_private_network=config.file_url_allow_private_network,
            trusted_private_hosts=config.file_url_trusted_private_hosts,
            trusted_private_cidrs=config.file_url_trusted_private_cidrs,
            max_redirects=config.file_url_max_redirects,
            max_download_bytes=config.file_url_max_download_bytes,
            chunk_size_bytes=config.file_url_chunk_size_bytes,
            connect_timeout_seconds=config.file_url_connect_timeout_seconds,
            read_timeout_seconds=config.file_url_read_timeout_seconds,
            write_timeout_seconds=config.file_url_write_timeout_seconds,
            pool_timeout_seconds=config.file_url_pool_timeout_seconds,
            dns_resolver=dns_resolver,
            transport=transport,
        )

    def _trusted_address(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(
            address.version == network.version and address in network
            for network in self.trusted_private_cidrs
        )

    def _validate_url(self, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise InputResolutionError("FILE_URL_INVALID", "file_url is invalid") from exc
        scheme = parsed.scheme.lower()
        if scheme not in self.allowed_schemes:
            raise InputResolutionError(
                "FILE_URL_SCHEME_NOT_ALLOWED", "Only HTTP and HTTPS file_url values are allowed"
            )
        if parsed.username is not None or parsed.password is not None:
            raise InputResolutionError(
                "FILE_URL_CREDENTIALS_NOT_ALLOWED", "Credentials embedded in file_url are not allowed"
            )
        host = (parsed.hostname or "").strip().rstrip(".").lower()
        if not host:
            raise InputResolutionError("FILE_URL_INVALID", "file_url hostname is required")
        try:
            literal = ipaddress.ip_address(host)
            addresses = [str(literal)]
        except ValueError:
            try:
                addresses = list(self.dns_resolver(
                    host, port or (443 if scheme == "https" else 80)
                ))
            except (OSError, socket.gaierror) as exc:
                raise InputResolutionError(
                    "FILE_URL_DNS_FAILED", "Unable to resolve file_url hostname"
                ) from exc
            if not addresses:
                raise InputResolutionError("FILE_URL_DNS_FAILED", "Unable to resolve file_url hostname")

        host_trusted = host in self.trusted_private_hosts
        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as exc:
                raise InputResolutionError(
                    "FILE_URL_DNS_FAILED", "Hostname resolved to an invalid address"
                ) from exc
            if address.is_multicast or address.is_unspecified or address.is_reserved:
                raise InputResolutionError(
                    "FILE_URL_HOST_NOT_ALLOWED",
                    "file_url hostname resolves to a prohibited address",
                )
            if not address.is_global:
                permitted = (
                    self.allow_private_network
                    or host_trusted
                    or self._trusted_address(address)
                )
                if not permitted:
                    raise InputResolutionError(
                        "FILE_URL_HOST_NOT_ALLOWED",
                        "file_url hostname resolves to a non-public address",
                    )
        return value

    @staticmethod
    def _content_length(response: httpx.Response) -> int | None:
        value = response.headers.get("content-length")
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _has_pdf_magic(prefix: bytes) -> bool:
        value = prefix[3:] if prefix.startswith(b"\xef\xbb\xbf") else prefix
        return value.lstrip(b"\x00\t\r\n ").startswith(b"%PDF-")

    @staticmethod
    def _validate_existing(path: Path, expected_sha256: str, expected_size: int) -> None:
        try:
            if path.stat().st_size != expected_size or sha256_file(path) != expected_sha256:
                raise InputResolutionError(
                    "FILE_STAGING_FAILED", "Existing staged file failed integrity validation"
                )
        except OSError as exc:
            raise InputResolutionError(
                "FILE_STAGING_FAILED", "Unable to validate staged file"
            ) from exc

    def _persist(self, partial: Path, digest: str, size_bytes: int) -> Path:
        destination = self.staging_root / "sha256" / digest[:2].lower() / f"{digest.lower()}.pdf"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                self._validate_existing(destination, digest, size_bytes)
                partial.unlink(missing_ok=True)
                return destination
            os.replace(partial, destination)
            return destination
        except InputResolutionError:
            raise
        except OSError as exc:
            raise InputResolutionError(
                "FILE_STAGING_FAILED", "Unable to persist downloaded file"
            ) from exc

    def _download(self, client: httpx.Client, initial_url: str) -> tuple[Path, str, int, str | None, str]:
        current_url = initial_url
        partial_dir = self.staging_root / ".partial"
        partial = partial_dir / f"{uuid4().hex}.partial"
        try:
            partial_dir.mkdir(parents=True, exist_ok=True)
            for redirect_count in range(self.max_redirects + 1):
                self._validate_url(current_url)
                LOG.info("Downloading file_url=%s", redact_url(current_url))
                try:
                    with client.stream("GET", current_url, headers={"Accept": "application/pdf,*/*;q=0.1"}) as response:
                        if response.status_code in REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise InputResolutionError(
                                    "FILE_DOWNLOAD_HTTP_ERROR", "Upstream redirect is missing Location"
                                )
                            if redirect_count >= self.max_redirects:
                                raise InputResolutionError(
                                    "FILE_URL_REDIRECT_LIMIT", "file_url redirect limit exceeded"
                                )
                            current_url = urljoin(current_url, location)
                            continue
                        if response.status_code < 200 or response.status_code >= 300:
                            raise InputResolutionError(
                                "FILE_DOWNLOAD_HTTP_ERROR",
                                f"Upstream returned HTTP {response.status_code}",
                            )
                        declared_size = self._content_length(response)
                        if declared_size is not None and declared_size > self.max_download_bytes:
                            raise InputResolutionError(
                                "FILE_TOO_LARGE", "Remote file exceeds the configured size limit"
                            )

                        digest = hashlib.sha256()
                        size_bytes = 0
                        prefix = bytearray()
                        with partial.open("xb") as output:
                            for chunk in response.iter_bytes(self.chunk_size_bytes):
                                if not chunk:
                                    continue
                                size_bytes += len(chunk)
                                if size_bytes > self.max_download_bytes:
                                    raise InputResolutionError(
                                        "FILE_TOO_LARGE", "Remote file exceeds the configured size limit"
                                    )
                                if len(prefix) < PDF_MAGIC_SCAN_BYTES:
                                    prefix.extend(chunk[:PDF_MAGIC_SCAN_BYTES - len(prefix)])
                                digest.update(chunk)
                                output.write(chunk)
                        if not self._has_pdf_magic(bytes(prefix)):
                            raise InputResolutionError(
                                "FILE_NOT_PDF", "Downloaded content is not a valid PDF"
                            )
                        hex_digest = digest.hexdigest().upper()
                        destination = self._persist(partial, hex_digest, size_bytes)
                        return (
                            destination,
                            hex_digest,
                            size_bytes,
                            response.headers.get("content-type"),
                            current_url,
                        )
                except httpx.TimeoutException as exc:
                    raise InputResolutionError(
                        "FILE_DOWNLOAD_TIMEOUT", "Timed out while downloading file_url"
                    ) from exc
                except httpx.RequestError as exc:
                    raise InputResolutionError(
                        "FILE_DOWNLOAD_FAILED", "Unable to download file_url"
                    ) from exc
            raise InputResolutionError("FILE_URL_REDIRECT_LIMIT", "file_url redirect limit exceeded")
        except InputResolutionError:
            raise
        except OSError as exc:
            raise InputResolutionError("FILE_STAGING_FAILED", "Unable to stage downloaded file") from exc
        finally:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                LOG.warning("Unable to clean partial staging file")

    def resolve(self, request: NormalizedReviewRequest) -> ResolvedInput:
        client = httpx.Client(
            transport=self.transport,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            path, digest, size_bytes, content_type, final_url = self._download(
                client, request.file_url
            )
        finally:
            client.close()
        return ResolvedInput(
            local_path=path,
            original_reference=redact_url(request.file_url),
            file_name=request.file_name,
            sha256=digest,
            size_bytes=size_bytes,
            content_type=content_type,
            final_url=redact_url(final_url),
        )
