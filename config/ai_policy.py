from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_safe


CONTENT_SIGNAL = "search=yes, ai-train=no, use=reference"
AI_TRAINING_CRAWLERS = (
    "Amazonbot",
    "Applebot-Extended",
    "Bytespider",
    "CCBot",
    "ClaudeBot",
    "Google-Extended",
    "GPTBot",
    "meta-externalagent",
)


def build_robots_txt(*, sitemap_url=None):
    lines = [
        "# AI training and fine-tuning are not permitted.",
        "# Search indexing and reference use are permitted.",
        "User-agent: *",
        f"Content-Signal: {CONTENT_SIGNAL}",
        "Allow: /",
    ]

    for crawler in AI_TRAINING_CRAWLERS:
        lines.extend(("", f"User-agent: {crawler}", "Disallow: /"))

    if sitemap_url:
        lines.extend(("", f"Sitemap: {sitemap_url}"))

    return "\n".join(lines) + "\n"


@require_safe
def robots_txt(request):
    return HttpResponse(
        build_robots_txt(sitemap_url=f"{settings.SITE_URL}/sitemap.xml"),
        content_type="text/plain; charset=utf-8",
    )
