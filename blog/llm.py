import json
import re
import urllib.error
import urllib.request

from django.conf import settings


OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


class DescriptionGenerationError(Exception):
    pass


def generate_post_description(post):
    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        raise DescriptionGenerationError(
            "Set OPENROUTER_API_KEY before generating descriptions."
        )

    max_chars = _description_max_chars()
    payload = {
        "model": settings.OPENROUTER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write concise descriptions for my personal blog posts. "
                    "Write in my voice, never in third person. Return only the "
                    "description, with no label and no quotes."
                ),
            },
            {
                "role": "user",
                "content": _description_prompt(post, max_chars),
            },
        ],
        "temperature": 0.3,
        "max_completion_tokens": 80,
    }

    request = urllib.request.Request(
        OPENROUTER_CHAT_COMPLETIONS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=_openrouter_headers(api_key),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise DescriptionGenerationError(
            f"OpenRouter returned HTTP {error.code}: {details}"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise DescriptionGenerationError(f"OpenRouter request failed: {error}") from error

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise DescriptionGenerationError("OpenRouter returned an unexpected response.") from error

    description = _clean_description(content)
    if not description:
        raise DescriptionGenerationError("OpenRouter returned an empty description.")

    return _truncate_description(description, max_chars)


def _description_prompt(post, max_chars):
    return (
        f"Write one plain-language description for my post in {max_chars} "
        "characters or fewer. Make it accurate and natural, not promotional. "
        "Do not refer to me as the author, the writer, or in third person. "
        "Avoid phrases like 'this article' or 'this post.' If the post is "
        "personal, use first person. One sentence is preferred.\n\n"
        f"Title: {post.title}\n\n"
        f"Article:\n{post.body}"
    )


def _description_max_chars():
    target = getattr(settings, "POST_DESCRIPTION_TARGET_CHARS", 155)
    return min(max(1, int(target)), 300)


def _openrouter_headers(api_key):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    site_url = getattr(settings, "OPENROUTER_SITE_URL", "")
    app_name = getattr(settings, "OPENROUTER_APP_NAME", "")
    if site_url:
        headers["HTTP-Referer"] = site_url
    if app_name:
        headers["X-Title"] = app_name

    return headers


def _clean_description(description):
    description = re.sub(r"\s+", " ", description).strip()
    return description.strip("\"'")


def _truncate_description(description, max_chars):
    if len(description) <= max_chars:
        return description

    truncated = description[: max_chars + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    if not truncated:
        truncated = description[:max_chars].rstrip(" ,;:-")
    return truncated[:max_chars]
