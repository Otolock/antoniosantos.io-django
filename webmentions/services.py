import ipaddress
import json
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


MAX_REDIRECTS = 5
MAX_SOURCE_BYTES = 1024 * 1024
MAX_URL_LENGTH = 500
SAFE_SCHEMES = {"http", "https"}
HTML_CONTENT_TYPES = {
    "",
    "application/xhtml+xml",
    "text/html",
}
LINK_ATTRIBUTES = (
    ("a", "href"),
    ("area", "href"),
    ("audio", "src"),
    ("embed", "src"),
    ("iframe", "src"),
    ("img", "src"),
    ("link", "href"),
    ("object", "data"),
    ("source", "src"),
    ("video", "src"),
)


class WebmentionValidationError(ValueError):
    pass


@dataclass
class SourceVerificationResult:
    links_to_target: bool
    source_deleted: bool = False


def validate_webmention_url(url):
    if len(url) > MAX_URL_LENGTH:
        raise WebmentionValidationError("URL is too long.")

    parsed = urlparse(url)
    if parsed.scheme not in SAFE_SCHEMES or not parsed.netloc:
        raise WebmentionValidationError("URL must be absolute http or https.")
    try:
        parsed.port
    except ValueError as error:
        raise WebmentionValidationError("URL port is invalid.") from error


def validate_source_url(url):
    validate_webmention_url(url)
    _validate_public_host(urlparse(url).hostname)


def verify_source_links_to_target(source_url, target_url):
    validate_source_url(source_url)
    validate_webmention_url(target_url)

    response = _fetch_source(source_url)
    if response is None:
        return SourceVerificationResult(False, source_deleted=True)

    content_type = _content_type(response)
    if (
        content_type not in HTML_CONTENT_TYPES
        and content_type != "text/plain"
        and not _is_json_content_type(content_type)
    ):
        response.close()
        return SourceVerificationResult(False)

    body = _read_limited_response(response)

    if content_type == "text/plain":
        return SourceVerificationResult(target_url in body)

    if _is_json_content_type(content_type):
        return SourceVerificationResult(_json_mentions_target(body, target_url))

    return SourceVerificationResult(
        _html_mentions_target(body, response.url, target_url)
    )


def source_links_to_target(source_url, target_url):
    return verify_source_links_to_target(source_url, target_url).links_to_target


def _fetch_source(source_url):
    session = requests.Session()
    session.max_redirects = MAX_REDIRECTS
    current_url = source_url

    for _ in range(MAX_REDIRECTS + 1):
        validate_source_url(current_url)
        try:
            response = session.get(
                current_url,
                allow_redirects=False,
                headers={
                    "Accept": (
                        "text/html, application/xhtml+xml, "
                        "application/json, text/plain"
                    ),
                    "User-Agent": "antoniosantos.io webmention receiver",
                },
                stream=True,
                timeout=10,
            )
        except requests.RequestException as error:
            raise WebmentionValidationError("Source URL could not be fetched.") from error

        if response.status_code in {410, 451}:
            response.close()
            return None

        if 300 <= response.status_code < 400:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise WebmentionValidationError("Source redirect has no Location.")
            current_url = urljoin(current_url, location)
            continue

        if response.status_code >= 400:
            response.close()
            raise WebmentionValidationError("Source URL could not be fetched.")

        return response

    raise WebmentionValidationError("Source URL redirects too many times.")


def _validate_public_host(hostname):
    if not hostname:
        raise WebmentionValidationError("URL must include a host.")

    try:
        addresses = _resolve_host_ips(hostname)
    except socket.gaierror as error:
        raise WebmentionValidationError("URL host could not be resolved.") from error

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise WebmentionValidationError("URL host must resolve publicly.")


def _resolve_host_ips(hostname):
    return {
        item[4][0]
        for item in socket.getaddrinfo(
            hostname,
            None,
            type=socket.SOCK_STREAM,
        )
    }


def _read_limited_response(response):
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_SOURCE_BYTES:
                raise WebmentionValidationError("Source response is too large.")
        except ValueError as error:
            raise WebmentionValidationError("Source Content-Length is invalid.") from error

    chunks = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=16384, decode_unicode=True):
            if not chunk:
                continue
            if isinstance(chunk, bytes):
                chunk_bytes = chunk
                chunk_text = chunk.decode(response.encoding or "utf-8")
            else:
                chunk_text = chunk
                chunk_bytes = chunk.encode(
                    response.encoding or "utf-8",
                    errors="ignore",
                )

            total += len(chunk_bytes)
            if total > MAX_SOURCE_BYTES:
                raise WebmentionValidationError("Source response is too large.")
            chunks.append(chunk_text)
    finally:
        response.close()

    return "".join(chunks)


def _content_type(response):
    return response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()


def _is_json_content_type(content_type):
    return content_type == "application/json" or content_type.endswith("+json")


def _html_mentions_target(body, base_url, target_url):
    soup = BeautifulSoup(body, "html.parser")

    for tag_name, attribute in LINK_ATTRIBUTES:
        for element in soup.find_all(tag_name):
            value = element.get(attribute)
            if value and urljoin(base_url, value) == target_url:
                return True

    return False


def _json_mentions_target(body, target_url):
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        raise WebmentionValidationError("Source JSON could not be parsed.")

    return _json_value_mentions_target(parsed, target_url)


def _json_value_mentions_target(value, target_url):
    if isinstance(value, str):
        return value == target_url
    if isinstance(value, dict):
        return any(
            _json_value_mentions_target(item, target_url)
            for item in value.values()
        )
    if isinstance(value, list):
        return any(_json_value_mentions_target(item, target_url) for item in value)
    return False
