from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from blog.models import Post

from .models import Webmention
from .services import (
    WebmentionValidationError,
    validate_webmention_url,
    verify_source_links_to_target,
)


ACCEPTED_STATIC_TARGETS = {
    "blog:archive",
    "blog:home",
    "blog:now",
    "blog:subscribe",
}


@csrf_exempt
@require_POST
def receive_webmention(request):
    source = request.POST.get("source", "").strip()
    target = request.POST.get("target", "").strip()

    if not source or not target:
        return HttpResponseBadRequest("Missing source or target.")

    try:
        _validate_request_urls(source, target)
        _validate_target_accepts_webmentions(target)
    except WebmentionValidationError as error:
        return HttpResponseBadRequest(str(error))

    try:
        result = verify_source_links_to_target(source, target)
    except WebmentionValidationError as error:
        _mark_rejected(source, target)
        return HttpResponseBadRequest(str(error))

    if not result.links_to_target:
        status = Webmention.DELETED if result.source_deleted else Webmention.REJECTED
        Webmention.objects.update_or_create(
            source_url=source,
            target_url=target,
            defaults={"status": status},
        )
        return HttpResponseBadRequest("Source does not link to target.")

    Webmention.objects.update_or_create(
        source_url=source,
        target_url=target,
        defaults={"status": Webmention.PENDING},
    )

    return HttpResponse("Webmention accepted.")


def _validate_request_urls(source, target):
    validate_webmention_url(source)
    validate_webmention_url(target)

    if source == target:
        raise WebmentionValidationError("Source and target must not be the same URL.")


def _validate_target_accepts_webmentions(target):
    parsed_target = urlparse(target)
    parsed_site = urlparse(settings.SITE_URL)

    if _normalized_netloc(parsed_target) != _normalized_netloc(parsed_site):
        raise WebmentionValidationError("Target URL is not on this site.")

    try:
        match = resolve(parsed_target.path or "/")
    except Resolver404 as error:
        raise WebmentionValidationError("Target URL does not accept Webmentions.") from error

    if match.view_name in ACCEPTED_STATIC_TARGETS:
        return

    if match.view_name == "blog:post_detail":
        slug = match.kwargs.get("slug")
        if Post.objects.filter(
            slug=slug,
            status=Post.PUBLISHED,
            published_at__lte=timezone.now(),
        ).exists():
            return

    raise WebmentionValidationError("Target URL does not accept Webmentions.")


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


def _mark_rejected(source, target):
    try:
        validate_webmention_url(source)
        validate_webmention_url(target)
        _validate_target_accepts_webmentions(target)
    except WebmentionValidationError:
        return

    Webmention.objects.update_or_create(
        source_url=source,
        target_url=target,
        defaults={"status": Webmention.REJECTED},
    )
