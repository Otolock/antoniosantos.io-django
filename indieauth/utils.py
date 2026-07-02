"""Helpers for IndieAuth: PKCE verification and client metadata discovery."""

import base64
import hashlib
import ipaddress
import json
import secrets
import socket
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup


MAX_METADATA_REDIRECTS = 3


def canonicalize_url(url):
    """Apply IndieAuth URL canonicalization (ensure a path, lowercase host)."""
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = parts.scheme or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return parts._replace(scheme=scheme, netloc=netloc, path=path).geturl()


def is_loopback_client(client_id):
    """Return True if the client_id host is a loopback address.

    The spec forbids fetching loopback client_ids (SSRF / it's the user's
    own machine).
    """
    host = (urlsplit(client_id).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_private_host(host):
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def is_safe_fetch_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return False

    try:
        port = parts.port
    except ValueError:
        return False

    host = parts.hostname
    if not host:
        return False

    if is_loopback_client(url) or is_private_host(host):
        return False

    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    for *_, sockaddr in addresses:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False

    return True


def verify_pkce(code_verifier, code_challenge, method):
    """Verify a PKCE code_verifier against the stored challenge."""
    if not code_challenge:
        # Code was issued without a challenge; verifier must be absent too.
        return not code_verifier

    if method == "S256":
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return secrets.compare_digest(computed, code_challenge)

    if method == "plain":
        return secrets.compare_digest(code_verifier, code_challenge)

    return False


def fetch_client_metadata(client_id, timeout=5):
    """Discover client metadata at the client_id URL.

    Returns a dict with keys: client_id, client_name, client_uri, logo_uri,
    redirect_uris (list). Returns None if the document could not be fetched
    or parsed. Loopback client_ids are not fetched.
    """
    if is_loopback_client(client_id):
        return {
            "client_id": client_id,
            "client_name": client_id,
            "client_uri": "",
            "logo_uri": "",
            "redirect_uris": [],
        }

    if not is_safe_fetch_url(client_id):
        return None

    resp = _safe_get(client_id, timeout=timeout)
    if resp is None:
        return None

    base = resp.url or client_id
    content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
    redirect_uris = []

    link_header_redirects = _parse_link_header_rel(resp.headers.get("Link", ""), "redirect_uri")

    metadata = {
        "client_id": client_id,
        "client_name": "",
        "client_uri": "",
        "logo_uri": "",
        "redirect_uris": list(link_header_redirects),
    }

    if content_type == "application/json":
        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            metadata["client_name"] = data.get("client_name", "") or ""
            metadata["client_uri"] = data.get("client_uri", "") or ""
            metadata["logo_uri"] = data.get("logo_uri", "") or ""
            for uri in data.get("redirect_uris", []) or []:
                if isinstance(uri, str):
                    redirect_uris.append(urljoin(base, uri))
    else:
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception:
            soup = None
        if soup is not None:
            # h-app / h-x-app microformat
            for rel in soup.find_all("link", attrs={"rel": "redirect_uri"}):
                href = rel.get("href")
                if href:
                    redirect_uris.append(urljoin(base, href))
            for a in soup.find_all("a", attrs={"rel": "redirect_uri"}):
                href = a.get("href")
                if href:
                    redirect_uris.append(urljoin(base, href))

            name_el = soup.find(class_="p-name") or soup.find("title")
            if name_el and name_el.get_text(strip=True):
                metadata["client_name"] = name_el.get_text(strip=True)
            logo_el = soup.find("img", class_="u-logo") or soup.find("img", class_="u-photo")
            if logo_el and logo_el.get("src"):
                metadata["logo_uri"] = urljoin(base, logo_el["src"])
            url_el = soup.find("a", class_="u-url")
            if url_el and url_el.get("href"):
                metadata["client_uri"] = urljoin(base, url_el["href"])

    for uri in redirect_uris:
        if uri not in metadata["redirect_uris"]:
            metadata["redirect_uris"].append(uri)

    if not metadata["client_name"]:
        metadata["client_name"] = client_id

    return metadata


def _safe_get(url, timeout):
    current_url = url

    for _ in range(MAX_METADATA_REDIRECTS + 1):
        if not is_safe_fetch_url(current_url):
            return None

        try:
            resp = requests.get(
                current_url,
                timeout=timeout,
                headers={"Accept": "application/json, text/html"},
                allow_redirects=False,
            )
        except requests.RequestException:
            return None

        if resp.is_redirect:
            location = resp.headers.get("Location")
            if not location:
                return None
            current_url = urljoin(current_url, location)
            continue

        return resp

    return None


def _parse_link_header_rel(header_value, rel_name):
    """Extract URLs from an HTTP Link header matching a given rel."""
    urls = []
    if not header_value:
        return urls
    for link in header_value.split(","):
        link = link.strip()
        if not link:
            continue
        try:
            target, rest = link.split(";", 1)
        except ValueError:
            continue
        target = target.strip().strip("<>")
        if f'rel="{rel_name}"' in rest or f"rel={rel_name}" in rest:
            urls.append(target)
    return urls


def redirect_uri_allowed(client_id, redirect_uri, client_metadata):
    """Validate redirect_uri per the spec.

    If scheme/host/port match the client_id, it is allowed. Otherwise it must
    appear in the client's published redirect_uris.
    """
    if not client_metadata:
        # Cannot verify a mismatched redirect; allow only exact host match.
        return _host_matches(client_id, redirect_uri)

    if _host_matches(client_id, redirect_uri):
        return True

    published = [
        canonicalize_url(uri)
        for uri in client_metadata.get("redirect_uris", [])
        if isinstance(uri, str)
    ]
    return redirect_uri in published


def _host_matches(client_id, redirect_uri):
    a = urlsplit(client_id)
    b = urlsplit(redirect_uri)
    try:
        a_port = a.port
        b_port = b.port
    except ValueError:
        return False
    return (a.scheme, a.hostname, a_port) == (b.scheme, b.hostname, b_port)
