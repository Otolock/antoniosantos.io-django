"""Micropub endpoint implementing the W3C Micropub spec.

https://www.w3.org/TR/micropub/

Supports: create (form/JSON/multipart), update (JSON), delete/undelete,
media endpoint, and querying (config, source, syndicate-to).
"""

import json
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from markdown import markdown

from blog.models import Post, RESERVED_POST_SLUGS
from indieauth.models import AccessToken
from .models import MediaUpload


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESERVED_FORM_PARAMS = {"access_token", "h", "action", "url", "q"}

# Properties we recognize and map to Post fields.
MEDIA_PROPERTIES = ("photo", "audio", "video")
ALLOWED_MEDIA_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".webm": "video/webm",
    ".webp": "image/webp",
}
DEFAULT_MEDIA_MAX_BYTES = 10 * 1024 * 1024

HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


@csrf_exempt
def micropub(request):
    if request.method == "GET":
        return _handle_get(request)
    if request.method == "POST":
        return _handle_post(request)
    return HttpResponseNotAllowed(["GET", "POST"])


@csrf_exempt
def media(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    token = _authenticate(request)
    if token is None:
        return _auth_error(request)
    if "create" not in token.scopes:
        return _error("insufficient_scope", "The create scope is required.", status=403)
    return _handle_media(request)


# ---------------------------------------------------------------------------
# GET: querying
# ---------------------------------------------------------------------------


def _handle_get(request):
    token = _authenticate(request)
    if token is None:
        return _auth_error(request)

    q = request.GET.get("q")
    if q == "config":
        return _query_config(request)
    if q == "source":
        if "update" not in token.scopes:
            return _error("insufficient_scope", "The update scope is required.", status=403)
        return _query_source(request)
    if q == "syndicate-to":
        return _query_syndicate_to(request)
    return _error("invalid_request", f"Unsupported query: {q}", status=400)


def _query_config(request):
    media_url = request.build_absolute_uri(reverse("micropub:media_endpoint"))
    return JsonResponse(
        {
            "media-endpoint": media_url,
            "post-types": [
                {"type": "article", "name": "Draft post"},
            ],
            "syndicate-to": [],
        }
    )


def _query_source(request):
    url = request.GET.get("url")
    if not url:
        return _error("invalid_request", "The url parameter is required.", status=400)

    post = _resolve_post(url)
    if post is None or post.status == Post.DELETED:
        return _error(
            "invalid_request", "The post with the requested URL was not found.", status=400
        )

    properties = _post_to_properties(post)

    # Filter to requested properties if specified.
    requested = request.GET.getlist("properties[]") or request.GET.getlist("properties")
    if requested:
        filtered = {k: v for k, v in properties.items() if k in requested}
        return JsonResponse({"properties": filtered})

    return JsonResponse({"type": ["h-entry"], "properties": properties})


def _query_syndicate_to(request):
    return JsonResponse({"syndicate-to": []})


# ---------------------------------------------------------------------------
# POST: create, update, delete, undelete
# ---------------------------------------------------------------------------


def _handle_post(request):
    token = _authenticate(request)
    if token is None:
        return _auth_error(request)

    action = _get_action(request)

    if action in (None, "create"):
        if "create" not in token.scopes:
            return _error("insufficient_scope", "The create scope is required.", status=403)
        return _handle_create(request)

    if action == "update":
        if "update" not in token.scopes:
            return _error("insufficient_scope", "The update scope is required.", status=403)
        return _handle_update(request)

    if action in ("delete", "undelete"):
        if "delete" not in token.scopes:
            return _error("insufficient_scope", "The delete scope is required.", status=403)
        return _handle_delete(request, action)

    return _error("invalid_request", f"Unsupported action: {action}", status=400)


def _get_action(request):
    """Extract the Micropub action from a form or JSON request."""
    content_type = request.headers.get("Content-Type", "")
    if content_type.startswith("application/json"):
        payload = _parse_json(request)
        if payload is None:
            return None
        return payload.get("action")
    return request.POST.get("action") or None


# --- Create ----------------------------------------------------------------


def _handle_create(request):
    try:
        properties = _properties_from_request(request)
    except ValueError as error:
        return _error("invalid_request", str(error), status=400)

    name = _first(properties.get("name", []))
    content = _extract_content(properties.get("content", []))
    media_md = _media_markdown(properties)

    full_content = content
    if media_md:
        full_content = f"{content}\n\n{media_md}".strip() if content else media_md

    if not full_content:
        return _error("invalid_request", "Post content is required.", status=400)

    title, body = title_and_body_from_leading_heading(full_content)
    if not title:
        title = name or title_from_content(full_content)
        body = full_content

    if not title:
        return _error("invalid_request", "Post content is required.", status=400)

    post = Post.objects.create(
        title=title,
        slug=unique_post_slug(title),
        body=body,
        status=Post.DRAFT,
        published_at=None,
    )

    response = HttpResponse(status=201)
    response["Location"] = request.build_absolute_uri(post.get_absolute_url())
    return response


def _properties_from_request(request):
    """Extract mf2-style properties from form, multipart, or JSON requests."""
    content_type = request.headers.get("Content-Type", "")
    if content_type.startswith("application/json"):
        return _properties_from_json(request)
    return _properties_from_form(request)


def _properties_from_form(request):
    if request.POST.get("h") not in {None, "", "entry"}:
        raise ValueError("Only h=entry posts are supported.")

    properties = {}

    for key in request.POST:
        if key in RESERVED_FORM_PARAMS or key.startswith("mp-"):
            continue
        values = request.POST.getlist(key)
        clean_key = key[:-2] if key.endswith("[]") else key

        # Non-standard but widely used: content[html] / content[value]
        if key == "content[html]":
            properties.setdefault("content", []).append({"html": values[0]})
        elif key == "content[value]":
            properties.setdefault("content", []).append({"value": values[0]})
        else:
            properties.setdefault(clean_key, []).extend(values)

    # File uploads (multipart) — store via the media backend and use the URL.
    for key in request.FILES:
        if key.startswith("mp-"):
            continue
        clean_key = key[:-2] if key.endswith("[]") else key
        for upload in request.FILES.getlist(key):
            _validate_media_upload(upload)
            media = MediaUpload.objects.create(file=upload)
            url = request.build_absolute_uri(media.file.url)
            properties.setdefault(clean_key, []).append(url)

    return properties


def _properties_from_json(request):
    payload = _parse_json(request)
    if payload is None:
        raise ValueError("Request body must be valid JSON.")

    if payload.get("type") and "h-entry" not in payload.get("type", []):
        raise ValueError("Only h-entry posts are supported.")

    properties = payload.get("properties", payload)
    if not isinstance(properties, dict):
        raise ValueError("properties must be a JSON object.")
    return {k: v for k, v in properties.items() if not k.startswith("mp-")}


# --- Update ----------------------------------------------------------------


def _handle_update(request):
    """Handle action=update (JSON only, spec §3.4)."""
    content_type = request.headers.get("Content-Type", "")
    if not content_type.startswith("application/json"):
        return _error("invalid_request", "Updates must be sent as JSON.", status=400)

    payload = _parse_json(request)
    if payload is None:
        return _error("invalid_request", "Updates must be sent as JSON.", status=400)

    url = payload.get("url")
    if not url:
        return _error("invalid_request", "The url property is required.", status=400)

    post = _resolve_post(url)
    if post is None or post.status == Post.DELETED:
        return _error(
            "invalid_request", "The post with the requested URL was not found.", status=400
        )

    replace = payload.get("replace", {})
    add = payload.get("add", {})
    delete = payload.get("delete")

    if not _is_property_map(replace):
        return _error(
            "invalid_request",
            "replace must be an object whose values are arrays.",
            status=400,
        )
    if not _is_property_map(add):
        return _error(
            "invalid_request",
            "add must be an object whose values are arrays.",
            status=400,
        )
    if delete is not None and not (
        isinstance(delete, list) or _is_property_map(delete)
    ):
        return _error(
            "invalid_request",
            "delete must be an array or an object whose values are arrays.",
            status=400,
        )

    if not replace and not add and delete is None:
        return _error(
            "invalid_request",
            "At least one of replace, add, or delete is required.",
            status=400,
        )

    properties = _post_to_properties(post)

    # Replace: set all values of a property.
    for key, values in replace.items():
        properties[key] = list(values)

    # Add: append values to a property.
    for key, values in add.items():
        properties.setdefault(key, []).extend(values)

    # Delete: remove properties or specific values.
    if isinstance(delete, list):
        for key in delete:
            properties.pop(key, None)
    elif isinstance(delete, dict):
        for key, values in delete.items():
            if key in properties:
                properties[key] = [v for v in properties[key] if v not in values]
                if not properties[key]:
                    del properties[key]

    _apply_properties_to_post(post, properties)
    post.save()

    return HttpResponse(status=200)


def _apply_properties_to_post(post, properties):
    """Map mf2 properties back onto a Post instance."""
    if "name" in properties:
        post.title = _first(properties["name"]) or post.title
    if "content" in properties:
        post.body = _extract_content(properties["content"])
    # Unrecognized properties (category, in-reply-to, etc.) are ignored.


def _is_property_map(value):
    return isinstance(value, dict) and all(
        isinstance(values, list) for values in value.values()
    )


# --- Delete / Undelete -----------------------------------------------------


def _handle_delete(request, action):
    url = _get_param(request, "url")
    if not url:
        return _error("invalid_request", "The url property is required.", status=400)

    post = _resolve_post(url)
    if post is None:
        return _error(
            "invalid_request", "The post with the requested URL was not found.", status=400
        )

    if action == "delete":
        if post.status == Post.DELETED:
            return _error("invalid_request", "The post is already deleted.", status=400)
        post.status = Post.DELETED
    else:  # undelete
        if post.status != Post.DELETED:
            return _error("invalid_request", "The post is not deleted.", status=400)
        post.status = Post.DRAFT

    post.save(update_fields=["status"])
    return HttpResponse(status=200)


def _get_param(request, name):
    """Get a parameter from JSON body or form POST."""
    content_type = request.headers.get("Content-Type", "")
    if content_type.startswith("application/json"):
        payload = _parse_json(request)
        if payload is None:
            return None
        return payload.get(name)
    return request.POST.get(name) or None


# ---------------------------------------------------------------------------
# Media endpoint
# ---------------------------------------------------------------------------


def _handle_media(request):
    uploaded = request.FILES.get("file")
    if not uploaded:
        return _error(
            "invalid_request", "A file part named 'file' is required.", status=400
        )

    try:
        _validate_media_upload(uploaded)
    except ValueError as error:
        return _error("invalid_request", str(error), status=400)

    media = MediaUpload.objects.create(file=uploaded)
    response = HttpResponse(status=201)
    response["Location"] = request.build_absolute_uri(media.file.url)
    return response


def _validate_media_upload(uploaded):
    max_bytes = getattr(settings, "MICROPUB_MEDIA_MAX_BYTES", DEFAULT_MEDIA_MAX_BYTES)
    if uploaded.size > max_bytes:
        raise ValueError("Uploaded media is too large.")

    suffix = ""
    if "." in uploaded.name:
        suffix = "." + uploaded.name.rsplit(".", 1)[-1].lower()

    expected_type = ALLOWED_MEDIA_TYPES.get(suffix)
    if expected_type is None:
        raise ValueError("Uploaded media type is not supported.")

    content_type = (uploaded.content_type or "").split(";", 1)[0].strip().lower()
    if content_type != expected_type:
        raise ValueError("Uploaded media content type does not match its extension.")


# ---------------------------------------------------------------------------
# Property <-> Post mapping
# ---------------------------------------------------------------------------


def _post_to_properties(post):
    """Serialize a Post into mf2-style properties for source queries."""
    properties = {
        "name": [post.title],
        "content": [post.body],
    }
    if post.published_at:
        properties["published"] = [post.published_at.isoformat()]
    return properties


def _first(values):
    if not values:
        return ""
    value = values[0]
    if isinstance(value, dict):
        return value.get("value") or value.get("html") or ""
    return str(value)


def _extract_content(content_values):
    """Extract a single content string from mf2 content property values."""
    if not content_values:
        return ""
    value = content_values[0]
    if isinstance(value, dict):
        return value.get("html") or value.get("value") or ""
    return str(value)


def _media_markdown(properties):
    """Build markdown for photo/audio/video URL properties."""
    parts = []
    for prop in MEDIA_PROPERTIES:
        for value in properties.get(prop, []):
            if isinstance(value, dict):
                url = value.get("value", "")
                alt = value.get("alt", "")
            else:
                url = str(value)
                alt = ""
            if not url:
                continue
            if prop == "photo":
                parts.append(f"![{alt}]({url})")
            else:
                parts.append(f'<{prop} src="{url}" controls></{prop}>')
    return "\n\n".join(parts)


def _resolve_post(url):
    """Resolve a post URL (absolute or path) to a Post via its slug."""
    if url.startswith(("http://", "https://")):
        path = urlsplit(url).path
    else:
        path = url
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None
    slug = segments[-1]
    try:
        return Post.objects.get(slug=slug)
    except Post.DoesNotExist:
        return None


def _parse_json(request):
    try:
        return json.loads(request.body.decode(request.encoding or "utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _extract_token(request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip()
    return request.POST.get("access_token") or request.GET.get("access_token") or ""


def _authenticate(request):
    """Resolve an IndieAuth-issued bearer token, or None."""
    token_value = _extract_token(request)
    if not token_value:
        return None
    try:
        token = AccessToken.objects.get(token=token_value)
    except AccessToken.DoesNotExist:
        return None
    return token if token.is_active else None


def _auth_error(request):
    """Return the correct error based on whether a token was provided."""
    if _extract_token(request):
        return _error(
            "invalid_token", "The access token is invalid or revoked.", status=401
        )
    response = _error("unauthorized", "An access token is required.", status=401)
    response["WWW-Authenticate"] = 'Bearer realm="micropub"'
    return response


def _error(error, description, status):
    return JsonResponse(
        {"error": error, "error_description": description},
        status=status,
    )


# ---------------------------------------------------------------------------
# Title / heading extraction (markdown-aware)
# ---------------------------------------------------------------------------


def title_and_body_from_leading_heading(content):
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    heading_index = _first_content_line_index(lines)
    if heading_index is None:
        return "", content

    heading = HEADING_RE.match(lines[heading_index])
    if heading:
        title = _plain_title(heading.group(2))
        body_lines = lines[:heading_index] + lines[heading_index + 1 :]
        return title, "\n".join(body_lines).lstrip("\n")

    if heading_index + 1 < len(lines) and SETEXT_RE.match(lines[heading_index + 1]):
        title = _plain_title(lines[heading_index].strip())
        body_lines = lines[:heading_index] + lines[heading_index + 2 :]
        return title, "\n".join(body_lines).lstrip("\n")

    return "", content




def _first_content_line_index(lines):
    for index, line in enumerate(lines):
        if line.strip():
            return index
    return None


def _plain_title(markdown_text):
    markdown_text = re.sub(r"\s+#+\s*$", "", markdown_text).strip()
    rendered = markdown(markdown_text)
    return strip_tags(rendered).strip()


def title_from_content(content):
    line_index = _first_content_line_index(content.splitlines())
    if line_index is None:
        return ""
    return _plain_title(content.splitlines()[line_index])[:200]


def unique_post_slug(title):
    max_length = Post._meta.get_field("slug").max_length
    base = slugify(title)[:max_length] or "post"
    if base in RESERVED_POST_SLUGS:
        base = f"{base}-post"[:max_length]

    candidate = base
    suffix = 2
    while Post.objects.filter(slug=candidate).exists():
        suffix_text = f"-{suffix}"
        candidate = f"{base[:max_length - len(suffix_text)]}{suffix_text}"
        suffix += 1

    return candidate
