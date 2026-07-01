import json
import re

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from markdown import markdown

from blog.models import Post, RESERVED_POST_SLUGS


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")


@csrf_exempt
def micropub(request):
    if request.method == "GET":
        return _handle_get(request)
    if request.method == "POST":
        return _handle_post(request)
    return HttpResponseNotAllowed(["GET", "POST"])


def _handle_get(request):
    if request.GET.get("q") != "config":
        return _error("invalid_request", "Only q=config is supported.", status=400)
    if not _is_authenticated(request):
        return _unauthorized()

    return JsonResponse(
        {
            "post-types": [
                {
                    "type": "article",
                    "name": "Draft post",
                },
            ],
        }
    )


def _handle_post(request):
    if not _is_authenticated(request):
        return _unauthorized()

    try:
        entry = _entry_from_request(request)
    except ValueError as error:
        return _error("invalid_request", str(error), status=400)

    content = entry.get("content", "").strip()
    if not content:
        return _error("invalid_request", "Post content is required.", status=400)

    title, body = title_and_body_from_leading_heading(content)
    if not title:
        return _error(
            "invalid_request",
            "Post content must start with a Markdown heading for the title.",
            status=400,
        )

    post = Post.objects.create(
        title=title,
        slug=unique_post_slug(title),
        body=body,
        status=Post.DRAFT,
        published_at=None,
    )

    response = JsonResponse({})
    response.status_code = 201
    response["Location"] = request.build_absolute_uri(post.get_absolute_url())
    return response


def _entry_from_request(request):
    content_type = request.headers.get("Content-Type", "")
    if content_type.startswith("application/json"):
        try:
            payload = json.loads(request.body.decode(request.encoding or "utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid JSON.") from error

        if payload.get("type") and "h-entry" not in payload.get("type", []):
            raise ValueError("Only h-entry posts are supported.")

        properties = payload.get("properties", payload)
        return {
            "content": _first_property_value(properties.get("content")),
        }

    if request.POST.get("h") not in {None, "", "entry"}:
        raise ValueError("Only h=entry posts are supported.")

    return {
        "content": (
            request.POST.get("content")
            or request.POST.get("content[value]")
            or request.POST.get("content[html]")
            or ""
        ),
    }


def _first_property_value(value):
    if isinstance(value, list):
        value = value[0] if value else ""

    if isinstance(value, dict):
        return value.get("value") or value.get("html") or ""

    if value is None:
        return ""

    return str(value)


def _is_authenticated(request):
    token = getattr(settings, "MICROPUB_TOKEN", "")
    if not token:
        return False

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip() == token

    return request.POST.get("access_token") == token


def _unauthorized():
    response = _error(
        "unauthorized",
        "A valid Micropub bearer token is required.",
        status=401,
    )
    response["WWW-Authenticate"] = 'Bearer realm="micropub"'
    return response


def _error(error, description, status):
    return JsonResponse(
        {
            "error": error,
            "error_description": description,
        },
        status=status,
    )


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
