import datetime
import re
import sys
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import migrations
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify


LOCAL_MEDIA_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
MARKDOWN_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)]+)\)")
PLAIN_LOCAL_IMAGE_RE = re.compile(
    r"(?P<prefix>['\"]?)(?P<url>(?:\.\./)?/??images/[^'\"\s)]+)(?P=prefix)"
)


def parse_frontmatter(markdown):
    if not markdown.startswith("---\n"):
        return {}, markdown

    end_marker = "\n---"
    end_index = markdown.find(end_marker, 4)
    if end_index == -1:
        return {}, markdown

    raw_frontmatter = markdown[4:end_index]
    body = markdown[end_index + len(end_marker) :].lstrip("\n")
    return parse_simple_yaml(raw_frontmatter), body


def parse_simple_yaml(raw_frontmatter):
    data = {}
    current_parent = None

    for raw_line in raw_frontmatter.splitlines():
        if not raw_line.strip():
            continue

        if raw_line.startswith((" ", "\t")) and current_parent:
            key, value = split_yaml_pair(raw_line.strip())
            data[current_parent][key] = clean_yaml_scalar(value)
            continue

        key, value = split_yaml_pair(raw_line)
        if value == "":
            data[key] = {}
            current_parent = key
        else:
            data[key] = clean_yaml_scalar(value)
            current_parent = None

    return data


def split_yaml_pair(line):
    key, separator, value = line.partition(":")
    if not separator:
        return line.strip(), ""
    return key.strip(), value.strip()


def clean_yaml_scalar(value):
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def parse_publish_time(value):
    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        if parsed_date is None:
            raise ValueError(f"Could not parse pubDate value: {value}")
        parsed = datetime.datetime.combine(parsed_date, datetime.time.min)

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def media_title(path):
    return path.stem.replace("-", " ").replace("_", " ").title()


def media_slug_for_path(path):
    return slugify(path.stem)[:220] or "media"


def normalized_media_name(url):
    url = url.split("#", 1)[0].split("?", 1)[0]
    name = Path(url).name
    if Path(name).suffix.lower() not in LOCAL_MEDIA_EXTENSIONS:
        return ""
    return name


def collect_media_references(body, frontmatter):
    references = {}

    image = frontmatter.get("image")
    if isinstance(image, dict):
        name = normalized_media_name(image.get("url", ""))
        if name:
            references[name] = image.get("alt", "")

    for match in MARKDOWN_IMAGE_RE.finditer(body):
        name = normalized_media_name(match.group("url"))
        if name:
            references.setdefault(name, match.group("alt").strip())

    for match in PLAIN_LOCAL_IMAGE_RE.finditer(body):
        name = normalized_media_name(match.group("url"))
        if name:
            references.setdefault(name, "")

    return references


def copy_media(PostMedia, media_path, alt_text):
    slug = media_slug_for_path(media_path)
    media, created = PostMedia.objects.get_or_create(
        slug=slug,
        defaults={
            "title": media_title(media_path),
            "alt_text": alt_text[:200],
        },
    )

    update_fields = []
    if alt_text and not media.alt_text:
        media.alt_text = alt_text[:200]
        update_fields.append("alt_text")
    if not media.title:
        media.title = media_title(media_path)
        update_fields.append("title")

    if created or not media.file:
        with media_path.open("rb") as handle:
            media.file.save(media_path.name, File(handle), save=False)
        media.save()
    elif update_fields:
        media.save(update_fields=update_fields)

    return media


def rewrite_media_links(body, media_by_name):
    def replace_markdown_image(match):
        name = normalized_media_name(match.group("url"))
        media = media_by_name.get(name)
        if not media:
            return match.group(0)
        return f"![{match.group('alt')}](/media/{media.slug}/)"

    body = MARKDOWN_IMAGE_RE.sub(replace_markdown_image, body)

    def replace_plain_local_image(match):
        name = normalized_media_name(match.group("url"))
        media = media_by_name.get(name)
        if not media:
            return match.group(0)
        prefix = match.group("prefix")
        return f"{prefix}/media/{media.slug}/{prefix}"

    return PLAIN_LOCAL_IMAGE_RE.sub(replace_plain_local_image, body)


def import_tmp_posts(apps, schema_editor):
    if "test" in sys.argv:
        return

    Post = apps.get_model("blog", "Post")
    PostMedia = apps.get_model("blog", "PostMedia")

    tmp_dir = Path(settings.BASE_DIR) / "tmp"
    if not tmp_dir.exists():
        print(f"Skipping blog import: {tmp_dir} does not exist.")
        return

    markdown_paths = sorted(tmp_dir.glob("*.md"))
    if not markdown_paths:
        print(f"Skipping blog import: no Markdown files found in {tmp_dir}.")
        return

    media_references = {}
    parsed_posts = []
    for markdown_path in markdown_paths:
        frontmatter, body = parse_frontmatter(markdown_path.read_text())
        parsed_posts.append((markdown_path, frontmatter, body))
        for name, alt_text in collect_media_references(body, frontmatter).items():
            media_references.setdefault(name, alt_text)

    media_by_name = {}
    for media_path in sorted(tmp_dir.iterdir()):
        if media_path.suffix.lower() not in LOCAL_MEDIA_EXTENSIONS:
            continue
        alt_text = media_references.get(media_path.name, "")
        media_by_name[media_path.name] = copy_media(PostMedia, media_path, alt_text)

    missing_media = sorted(set(media_references) - set(media_by_name))
    if missing_media:
        print(
            "Blog import warning: missing media in tmp/: "
            + ", ".join(missing_media)
        )

    for markdown_path, frontmatter, body in parsed_posts:
        title = frontmatter.get("title") or markdown_path.stem.replace("-", " ").title()
        description = frontmatter.get("description", "")[:300]
        published_at = parse_publish_time(frontmatter["pubDate"])
        body = rewrite_media_links(body, media_by_name)

        Post.objects.update_or_create(
            slug=markdown_path.stem,
            defaults={
                "title": title,
                "body": body,
                "description": description,
                "status": "published",
                "published_at": published_at,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0004_postmedia_slug"),
    ]

    operations = [
        migrations.RunPython(import_tmp_posts, migrations.RunPython.noop),
    ]
