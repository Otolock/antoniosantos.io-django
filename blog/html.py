from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment


ALLOWED_TAGS = {
    "a",
    "audio",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "source",
    "strong",
    "ul",
    "video",
}

DROP_WITH_CONTENT = {"embed", "iframe", "math", "object", "script", "style", "svg"}
GLOBAL_ATTRIBUTES = {"title"}
TAG_ATTRIBUTES = {
    "a": {"href", "rel"},
    "audio": {"controls", "src"},
    "img": {"alt", "height", "src", "width"},
    "source": {"src", "type"},
    "video": {"controls", "height", "src", "width"},
}
URL_ATTRIBUTES = {"href", "src"}
SAFE_SCHEMES = {"http", "https", "mailto"}


def sanitize_html(html):
    soup = BeautifulSoup(html, "html.parser")

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for tag in soup.find_all(True):
        if tag.name not in ALLOWED_TAGS:
            if tag.name in DROP_WITH_CONTENT:
                tag.decompose()
            else:
                tag.unwrap()
            continue

        allowed_attrs = GLOBAL_ATTRIBUTES | TAG_ATTRIBUTES.get(tag.name, set())
        for attr, value in list(tag.attrs.items()):
            if attr.startswith("on") or attr not in allowed_attrs:
                del tag.attrs[attr]
                continue

            if attr in URL_ATTRIBUTES and not _is_safe_url(value):
                del tag.attrs[attr]

    return str(soup)


def _is_safe_url(value):
    if isinstance(value, list):
        value = " ".join(value)
    value = str(value).strip()
    if not value:
        return False

    parts = urlsplit(value)
    if parts.netloc and not parts.scheme:
        return False
    if parts.scheme and parts.scheme.lower() not in SAFE_SCHEMES:
        return False
    return True
