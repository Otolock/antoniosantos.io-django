import ipaddress
import json
import socket
import threading
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone
import requests
from bs4 import BeautifulSoup

from .models import SentWebmention


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


@dataclass
class SendWebmentionResult:
    target_url: str
    endpoint_url: str = ""
    status: str = SentWebmention.PENDING
    response_code: int | None = None
    error: str = ""


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


def send_webmentions_for_post(post):
    records = queue_webmentions_for_post(post)
    return [send_webmention(record.source_url, record.target_url) for record in records]


def queue_webmentions_for_post(post):
    if not post.is_published:
        return []

    source_url = post_source_url(post)
    records = []
    for target_url in post_webmention_targets(post, source_url):
        record, created = SentWebmention.objects.get_or_create(
            source_url=source_url,
            target_url=target_url,
        )
        if created:
            records.append(record)
    return records


def send_webmentions_for_post_async(post):
    if not post.pk or not post.is_published:
        return

    post_id = post.pk

    def start_sender():
        thread = threading.Thread(
            target=_send_webmentions_for_post_id,
            args=(post_id,),
            daemon=True,
        )
        thread.start()

    transaction.on_commit(start_sender)


def _send_webmentions_for_post_id(post_id):
    from blog.models import Post

    close_old_connections()
    try:
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return

        send_webmentions_for_post(post)
    finally:
        close_old_connections()


def post_source_url(post):
    return urljoin(settings.SITE_URL + "/", post.get_absolute_url().lstrip("/"))


def post_webmention_targets(post, source_url=None):
    source_url = source_url or post_source_url(post)
    source_netloc = _normalized_netloc(urlparse(source_url))
    targets = []
    seen = set()

    if post.reply_to_url:
        _append_webmention_target(
            targets,
            seen,
            urljoin(source_url, post.reply_to_url.strip()),
            source_url,
            source_netloc,
        )

    for target_url in extract_webmention_targets(post.body_html, source_url):
        _append_webmention_target(
            targets,
            seen,
            target_url,
            source_url,
            source_netloc,
        )

    return targets


def extract_webmention_targets(html, source_url):
    source_netloc = _normalized_netloc(urlparse(source_url))
    soup = BeautifulSoup(html, "html.parser")
    targets = []
    seen = set()

    for link in soup.find_all("a", href=True):
        _append_webmention_target(
            targets,
            seen,
            urljoin(source_url, link["href"]),
            source_url,
            source_netloc,
        )

    return targets


def _append_webmention_target(targets, seen, target_url, source_url, source_netloc):
    try:
        validate_webmention_url(target_url)
    except WebmentionValidationError:
        return

    if target_url == source_url:
        return

    if _normalized_netloc(urlparse(target_url)) == source_netloc:
        return

    if target_url not in seen:
        targets.append(target_url)
        seen.add(target_url)


def send_webmention(source_url, target_url):
    record, _ = SentWebmention.objects.get_or_create(
        source_url=source_url,
        target_url=target_url,
    )

    try:
        endpoint_url = discover_webmention_endpoint(target_url)
    except WebmentionValidationError as error:
        return _record_sent_webmention_result(
            record,
            SendWebmentionResult(
                target_url=target_url,
                status=SentWebmention.FAILED,
                error=str(error),
            ),
        )

    if not endpoint_url:
        return _record_sent_webmention_result(
            record,
            SendWebmentionResult(
                target_url=target_url,
                status=SentWebmention.NO_ENDPOINT,
                error="Target does not advertise a Webmention endpoint.",
            ),
        )

    try:
        validate_source_url(endpoint_url)
        response = requests.post(
            endpoint_url,
            data={"source": source_url, "target": target_url},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "antoniosantos.io webmention sender",
            },
            timeout=10,
            allow_redirects=False,
        )
    except requests.RequestException as error:
        return _record_sent_webmention_result(
            record,
            SendWebmentionResult(
                target_url=target_url,
                endpoint_url=endpoint_url,
                status=SentWebmention.FAILED,
                error=str(error),
            ),
        )
    except WebmentionValidationError as error:
        return _record_sent_webmention_result(
            record,
            SendWebmentionResult(
                target_url=target_url,
                endpoint_url=endpoint_url,
                status=SentWebmention.FAILED,
                error=str(error),
            ),
        )

    status = (
        SentWebmention.SENT
        if 200 <= response.status_code < 300
        else SentWebmention.FAILED
    )
    return _record_sent_webmention_result(
        record,
        SendWebmentionResult(
            target_url=target_url,
            endpoint_url=endpoint_url,
            status=status,
            response_code=response.status_code,
            error="" if status == SentWebmention.SENT else response.text[:1000],
        ),
    )


def discover_webmention_endpoint(target_url):
    validate_source_url(target_url)

    response = _fetch_source(target_url)
    if response is None:
        return ""

    endpoint_url = _endpoint_from_link_header(response)
    if endpoint_url:
        response.close()
        return endpoint_url

    content_type = _content_type(response)
    if content_type not in HTML_CONTENT_TYPES:
        response.close()
        return ""

    body = _read_limited_response(response)
    return _endpoint_from_html(body, response.url)


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


def _endpoint_from_link_header(response):
    link_header = response.headers.get("Link", "")
    for link_value in _split_link_header(link_header):
        endpoint_url = _webmention_endpoint_from_link_value(link_value, response.url)
        if endpoint_url:
            return endpoint_url
    return ""


def _split_link_header(link_header):
    parts = []
    current = []
    in_angle_brackets = False
    in_quotes = False

    for character in link_header:
        if character == "<" and not in_quotes:
            in_angle_brackets = True
        elif character == ">" and not in_quotes:
            in_angle_brackets = False
        if character == '"':
            in_quotes = not in_quotes
        if character == "," and not in_quotes and not in_angle_brackets:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(character)

    if current:
        parts.append("".join(current).strip())

    return parts


def _webmention_endpoint_from_link_value(link_value, base_url):
    if not link_value.startswith("<") or ">" not in link_value:
        return ""

    endpoint, parameter_text = link_value[1:].split(">", 1)
    rels = set()
    for parameter in parameter_text.split(";"):
        name, separator, value = parameter.strip().partition("=")
        if separator and name.lower() == "rel":
            rels.update(rel.lower() for rel in value.strip('"').split())

    if "webmention" in rels:
        return urljoin(base_url, endpoint)

    return ""


def _endpoint_from_html(body, base_url):
    soup = BeautifulSoup(body, "html.parser")
    for element in soup.find_all(["link", "a"]):
        rels = element.get("rel") or []
        if isinstance(rels, str):
            rels = rels.split()
        rels = [rel.lower() for rel in rels]
        if "webmention" in rels and element.get("href"):
            return urljoin(base_url, element["href"])
    return ""


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


def _normalized_netloc(parsed_url):
    hostname = (parsed_url.hostname or "").lower()
    port = parsed_url.port
    if (
        (parsed_url.scheme == "http" and port == 80)
        or (parsed_url.scheme == "https" and port == 443)
        or port is None
    ):
        return hostname
    return f"{hostname}:{port}"


def _record_sent_webmention_result(record, result):
    record.endpoint_url = result.endpoint_url
    record.status = result.status
    record.response_code = result.response_code
    record.error = result.error[:2000]
    record.last_sent_at = timezone.now()
    SentWebmention.objects.filter(pk=record.pk).update(
        attempts=F("attempts") + 1,
        endpoint_url=record.endpoint_url,
        status=record.status,
        response_code=record.response_code,
        error=record.error,
        last_sent_at=record.last_sent_at,
    )
    record.refresh_from_db()
    return result
