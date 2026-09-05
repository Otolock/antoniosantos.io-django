from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment
from markdown import markdown


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
    "sup",
    "ul",
    "video",
}

DROP_WITH_CONTENT = {"embed", "iframe", "math", "object", "script", "style", "svg"}
GLOBAL_ATTRIBUTES = {"title"}
TAG_ATTRIBUTES = {
    "a": {"href", "rel"},
    "audio": {"controls", "src"},
    "img": {"alt", "height", "src", "width"},
    "li": {"id"},
    "sup": {"id"},
    "source": {"src", "type"},
    "video": {"controls", "height", "src", "width"},
}
URL_ATTRIBUTES = {"href", "src"}
SAFE_SCHEMES = {"http", "https", "mailto"}


def render_markdown(body):
    soup = BeautifulSoup(markdown(body, extensions=["footnotes"]), "html.parser")
    # A footnote containing only a source URL should link to that source.
    for paragraph in soup.select(".footnote li > p"):
        node = paragraph.contents[0] if paragraph.contents else None
        if not isinstance(node, str):
            continue
        url = node.strip()
        if not url or any(char.isspace() for char in url):
            continue
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            continue
        link = soup.new_tag("a", href=url)
        link.string = url
        node.replace_with(link)
        link.insert_after(" ")
    return sanitize_html(str(soup))


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
