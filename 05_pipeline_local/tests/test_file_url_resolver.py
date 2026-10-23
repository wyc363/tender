from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from review_pipeline.http.app import create_app
from review_pipeline.http.input_resolver import (
    InputResolutionError,
    SafeFileUrlResolver,
    redact_url,
)
from review_pipeline.http.request_adapter import NormalizedReviewRequest
from review_pipeline.job_store import JobStore
from test_http_api import make_config, request_body


PUBLIC_IP = "93.184.216.34"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


def normalized(url: str, name: str | None = "sample.pdf") -> NormalizedReviewRequest:
    return NormalizedReviewRequest(
        chat_id="CHAT-DOWNLOAD",
        file_url=url,
        file_name=name,
        stream=False,
        detail=False,
        variables={},
    )


def public_dns(host: str, port: int | None) -> list[str]:
    return [PUBLIC_IP]


def resolver(tmp_path: Path, handler, **overrides) -> SafeFileUrlResolver:
    values = {
        "staging_root": tmp_path / "staging",
        "dns_resolver": public_dns,
        "transport": httpx.MockTransport(handler),
        "max_download_bytes": 1024 * 1024,
        "chunk_size_bytes": 7,
    }
    values.update(overrides)
    return SafeFileUrlResolver(**values)


def assert_partial_clean(root: Path) -> None:
    partial = root / "staging" / ".partial"
    assert not partial.exists() or not list(partial.iterdir())


@pytest.mark.parametrize("url", [
    "https://public.example/a.pdf",
    "http://public.example/a.pdf",
])
def test_http_and_https_stream_to_content_addressed_pdf(url, tmp_path):
    content_type = "application/octet-stream" if url.startswith("http://") else "application/pdf"
    result = resolver(
        tmp_path,
        lambda request: httpx.Response(200, content=PDF, headers={"content-type": content_type}),
    ).resolve(normalized(url))
    expected = hashlib.sha256(PDF).hexdigest().upper()
    assert result.sha256 == expected
    assert result.size_bytes == len(PDF)
    assert result.content_type == content_type
    assert result.local_path.read_bytes() == PDF
    assert result.local_path.name == f"{expected.lower()}.pdf"
    assert result.local_path.parent.name == expected[:2].lower()
    assert_partial_clean(tmp_path)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("file:///etc/passwd", "FILE_URL_SCHEME_NOT_ALLOWED"),
        ("ftp://public.example/a.pdf", "FILE_URL_SCHEME_NOT_ALLOWED"),
        ("data:application/pdf,x", "FILE_URL_SCHEME_NOT_ALLOWED"),
        ("https:///a.pdf", "FILE_URL_INVALID"),
        ("https://user:pass@public.example/a.pdf", "FILE_URL_CREDENTIALS_NOT_ALLOWED"),
        ("https://public.example:99999/a.pdf", "FILE_URL_INVALID"),
    ],
)
def test_invalid_url_contract_is_rejected_before_transport(url, code, tmp_path):
    calls = []
    instance = resolver(tmp_path, lambda request: calls.append(request))
    with pytest.raises(InputResolutionError) as caught:
        instance.resolve(normalized(url))
    assert caught.value.code == code
    assert calls == []
    assert_partial_clean(tmp_path)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a.pdf",
    "http://10.1.2.3/a.pdf",
    "http://192.168.1.2/a.pdf",
    "http://169.254.1.2/a.pdf",
    "http://[::1]/a.pdf",
    "http://[fe80::1]/a.pdf",
    "http://224.0.0.1/a.pdf",
    "http://[ff02::1]/a.pdf",
    "http://0.0.0.0/a.pdf",
])
def test_ip_literals_for_non_public_ranges_are_blocked(url, tmp_path):
    with pytest.raises(InputResolutionError) as caught:
        resolver(tmp_path, lambda request: httpx.Response(200, content=PDF)).resolve(normalized(url))
    assert caught.value.code == "FILE_URL_HOST_NOT_ALLOWED"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.8", "192.168.2.2", "::1", "fe80::5"])
def test_dns_resolving_to_non_public_address_is_blocked(address, tmp_path):
    instance = resolver(
        tmp_path,
        lambda request: httpx.Response(200, content=PDF),
        dns_resolver=lambda host, port: [address],
    )
    with pytest.raises(InputResolutionError) as caught:
        instance.resolve(normalized("https://localhost/a.pdf"))
    assert caught.value.code == "FILE_URL_HOST_NOT_ALLOWED"


def test_dns_failure_is_mapped_without_exposing_host_details(tmp_path):
    def failed_dns(host, port):
        raise OSError("secret resolver details")

    with pytest.raises(InputResolutionError) as caught:
        resolver(tmp_path, lambda request: None, dns_resolver=failed_dns).resolve(
            normalized("https://public.example/a.pdf")
        )
    assert caught.value.code == "FILE_URL_DNS_FAILED"
    assert "secret" not in str(caught.value)


def test_public_redirect_is_followed_and_each_host_is_resolved(tmp_path):
    resolved_hosts = []
    requested = []

    def dns(host, port):
        resolved_hosts.append(host)
        return [PUBLIC_IP]

    def handler(request):
        requested.append(str(request.url))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "https://cdn.example/final.pdf"})
        return httpx.Response(200, content=PDF)

    result = resolver(tmp_path, handler, dns_resolver=dns).resolve(
        normalized("https://public.example/start")
    )
    assert result.local_path.is_file()
    assert resolved_hosts == ["public.example", "cdn.example"]
    assert requested == ["https://public.example/start", "https://cdn.example/final.pdf"]


def test_redirect_to_private_host_is_blocked_before_second_request(tmp_path):
    requests = []

    def dns(host, port):
        return [PUBLIC_IP] if host == "public.example" else ["127.0.0.1"]

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})

    with pytest.raises(InputResolutionError) as caught:
        resolver(tmp_path, handler, dns_resolver=dns).resolve(
            normalized("https://public.example/a.pdf")
        )
    assert caught.value.code == "FILE_URL_HOST_NOT_ALLOWED"
    assert requests == ["https://public.example/a.pdf"]


def test_redirect_limit_is_enforced(tmp_path):
    def handler(request):
        number = int(request.url.path.strip("/") or "0")
        return httpx.Response(302, headers={"location": f"/{number + 1}"})

    with pytest.raises(InputResolutionError) as caught:
        resolver(tmp_path, handler, max_redirects=2).resolve(
            normalized("https://public.example/0")
        )
    assert caught.value.code == "FILE_URL_REDIRECT_LIMIT"


def test_trusted_private_host_and_cidr_are_explicit_admin_only_overrides(tmp_path):
    host = resolver(
        tmp_path / "host",
        lambda request: httpx.Response(200, content=PDF),
        dns_resolver=lambda name, port: ["10.2.3.4"],
        trusted_private_hosts=["files.internal.example"],
    )
    cidr = resolver(
        tmp_path / "cidr",
        lambda request: httpx.Response(200, content=PDF),
        dns_resolver=lambda name, port: ["10.2.3.4"],
        trusted_private_cidrs=["10.2.0.0/16"],
    )
    assert host.resolve(normalized("https://files.internal.example/a.pdf")).local_path.is_file()
    assert cidr.resolve(normalized("https://another.internal.example/a.pdf")).local_path.is_file()


def test_declared_pdf_with_html_body_is_rejected_and_partial_removed(tmp_path):
    instance = resolver(
        tmp_path,
        lambda request: httpx.Response(
            200, content=b"<html>not pdf</html>", headers={"content-type": "application/pdf"}
        ),
    )
    with pytest.raises(InputResolutionError) as caught:
        instance.resolve(normalized("https://public.example/a.pdf"))
    assert caught.value.code == "FILE_NOT_PDF"
    assert_partial_clean(tmp_path)


def test_html_with_embedded_pdf_marker_is_still_rejected(tmp_path):
    body = b"<html>error page %PDF- fake marker</html>"
    with pytest.raises(InputResolutionError) as caught:
        resolver(tmp_path, lambda request: httpx.Response(200, content=body)).resolve(
            normalized("https://public.example/a.pdf")
        )
    assert caught.value.code == "FILE_NOT_PDF"


def test_small_bom_or_whitespace_before_pdf_magic_is_allowed(tmp_path):
    body = b"\xef\xbb\xbf \r\n%PDF-1.7\n%%EOF"
    result = resolver(tmp_path, lambda request: httpx.Response(200, content=body)).resolve(
        normalized("https://public.example/a.pdf")
    )
    assert result.local_path.read_bytes() == body


@pytest.mark.parametrize("status", [403, 404, 500])
def test_upstream_http_error_is_technical(status, tmp_path):
    with pytest.raises(InputResolutionError) as caught:
        resolver(tmp_path, lambda request: httpx.Response(status)).resolve(
            normalized("https://public.example/a.pdf")
        )
    assert caught.value.code == "FILE_DOWNLOAD_HTTP_ERROR"
    assert str(status) in str(caught.value)


@pytest.mark.parametrize(
    ("exception_factory", "code"),
    [
        (lambda request: httpx.ConnectError("signed-token=SECRET", request=request), "FILE_DOWNLOAD_FAILED"),
        (lambda request: httpx.ReadTimeout("signed-token=SECRET", request=request), "FILE_DOWNLOAD_TIMEOUT"),
    ],
)
def test_connection_and_timeout_errors_are_sanitized(exception_factory, code, tmp_path):
    def handler(request):
        raise exception_factory(request)

    with pytest.raises(InputResolutionError) as caught:
        resolver(tmp_path, handler).resolve(
            normalized("https://public.example/a.pdf?token=TOPSECRET")
        )
    assert caught.value.code == code
    assert "SECRET" not in str(caught.value)
    assert_partial_clean(tmp_path)


def test_content_length_limit_rejects_before_writing(tmp_path):
    response = lambda request: httpx.Response(
        200, content=PDF, headers={"content-length": "1000"}
    )
    with pytest.raises(InputResolutionError) as caught:
        resolver(tmp_path, response, max_download_bytes=20).resolve(
            normalized("https://public.example/a.pdf")
        )
    assert caught.value.code == "FILE_TOO_LARGE"
    assert_partial_clean(tmp_path)


def test_actual_stream_limit_without_content_length_removes_partial(tmp_path):
    with pytest.raises(InputResolutionError) as caught:
        resolver(
            tmp_path,
            lambda request: httpx.Response(200, stream=ChunkStream([PDF[:12], PDF[12:]])),
            max_download_bytes=len(PDF) - 1,
        ).resolve(normalized("https://public.example/a.pdf"))
    assert caught.value.code == "FILE_TOO_LARGE"
    assert_partial_clean(tmp_path)


def test_duplicate_content_reuses_same_staged_path(tmp_path):
    instance = resolver(tmp_path, lambda request: httpx.Response(200, content=PDF))
    first = instance.resolve(normalized("https://public.example/a.pdf"))
    first_mtime = first.local_path.stat().st_mtime_ns
    second = instance.resolve(normalized("https://cdn.example/different-signed.pdf?sig=2"))
    assert first.local_path == second.local_path
    assert second.local_path.stat().st_mtime_ns == first_mtime
    assert_partial_clean(tmp_path)


def test_client_filename_never_controls_disk_path(tmp_path):
    result = resolver(tmp_path, lambda request: httpx.Response(200, content=PDF)).resolve(
        normalized("https://public.example/a", "../../outside/evil.pdf")
    )
    assert result.local_path.is_relative_to((tmp_path / "staging").resolve())
    assert "evil" not in result.local_path.name
    assert not (tmp_path / "outside").exists()


def test_signed_query_is_redacted_in_metadata_and_logs(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="review_pipeline.http.file_url")
    url = "https://public.example/a.pdf?X-Amz-Signature=TOPSECRET&token=ALSOSECRET"
    result = resolver(tmp_path, lambda request: httpx.Response(200, content=PDF)).resolve(
        normalized(url)
    )
    assert result.original_reference.endswith("?REDACTED")
    assert result.final_url and result.final_url.endswith("?REDACTED")
    assert "TOPSECRET" not in caplog.text
    assert "ALSOSECRET" not in caplog.text
    assert "REDACTED" in caplog.text
    assert redact_url(url) == "https://public.example/a.pdf?REDACTED"


def test_httpx_and_httpcore_log_records_redact_signed_query(tmp_path, caplog):
    resolver(tmp_path, lambda request: httpx.Response(200, content=PDF))
    caplog.set_level(logging.DEBUG)
    logging.getLogger("httpx").info(
        "HTTP Request GET https://public.example/a.pdf?signature=TOPSECRET&token=SECOND"
    )
    logging.getLogger("httpcore").warning(
        "request target=/a.pdf?X-Amz-Signature=THIRDSECRET"
    )
    assert "TOPSECRET" not in caplog.text
    assert "SECOND" not in caplog.text
    assert "THIRDSECRET" not in caplog.text
    assert "REDACTED" in caplog.text


def test_staging_failure_is_mapped_and_partial_removed(tmp_path, monkeypatch):
    def fail_replace(source, destination):
        raise OSError("private path and secret")

    monkeypatch.setattr("review_pipeline.http.input_resolver.os.replace", fail_replace)
    with pytest.raises(InputResolutionError) as caught:
        resolver(tmp_path, lambda request: httpx.Response(200, content=PDF)).resolve(
            normalized("https://public.example/a.pdf")
        )
    assert caught.value.code == "FILE_STAGING_FAILED"
    assert "private" not in str(caught.value)
    assert_partial_clean(tmp_path)


def test_default_production_service_uses_safe_resolver(tmp_path):
    config = make_config(tmp_path)
    app = create_app(config)
    assert isinstance(app.state.review_service.input_resolver, SafeFileUrlResolver)
    assert app.state.worker_started is False


def test_fastapi_mock_download_stages_pdf_and_creates_queued_job(tmp_path, monkeypatch):
    from review_pipeline.orchestrator import PipelineOrchestrator

    monkeypatch.setattr(
        PipelineOrchestrator,
        "run_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("POST must not run Pipeline")),
    )
    config = make_config(tmp_path)
    store = JobStore(config.effective_job_database_path)
    safe = SafeFileUrlResolver.from_config(
        config,
        dns_resolver=public_dns,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=PDF)),
    )
    app = create_app(config, job_store=store, input_resolver=safe)
    client = TestClient(app)
    response = client.post(
        "/api/v1/review/jobs",
        json=request_body(
            chat_id="CHAT-STAGED",
            items=[{
                "type": "file_url",
                "name": "../../unsafe.pdf",
                "url": "https://public.example/tender.pdf?signature=SECRET",
            }],
        ),
    )
    assert response.status_code == 202
    body = response.json()
    assert body["chat_id"] == "CHAT-STAGED"
    assert body["status"] == "QUEUED"
    assert body["stage"] == "SUBMITTED"
    job = store.get_job(body["job_id"])
    source = Path(job.source_path)
    assert source.is_file() and source.read_bytes() == PDF
    assert source.is_relative_to(config.effective_staging_root)
    assert job.file_sha256 == hashlib.sha256(PDF).hexdigest().upper()
    assert client.get(f"/api/v1/review/jobs/{body['job_id']}").status_code == 200
    assert app.state.worker_started is False


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("FILE_URL_SCHEME_NOT_ALLOWED", 422),
        ("FILE_URL_CREDENTIALS_NOT_ALLOWED", 422),
        ("FILE_URL_HOST_NOT_ALLOWED", 403),
        ("FILE_URL_DNS_FAILED", 502),
        ("FILE_DOWNLOAD_FAILED", 502),
        ("FILE_DOWNLOAD_TIMEOUT", 504),
        ("FILE_DOWNLOAD_HTTP_ERROR", 502),
        ("FILE_URL_REDIRECT_LIMIT", 502),
        ("FILE_TOO_LARGE", 413),
        ("FILE_NOT_PDF", 422),
        ("FILE_STAGING_FAILED", 500),
    ],
)
def test_http_error_mapping_for_resolver_errors(code, status, tmp_path):
    class FailingResolver:
        def resolve(self, request):
            raise InputResolutionError(code, "safe technical message")

    config = make_config(tmp_path)
    app = create_app(config, input_resolver=FailingResolver())
    response = TestClient(app).post("/api/v1/review/jobs", json=request_body())
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "is_pass" not in response.text
