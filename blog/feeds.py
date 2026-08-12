from django.conf import settings
from django.contrib.syndication.views import Feed
from django.template.loader import render_to_string
from django.utils.feedgenerator import Rss201rev2Feed
from django.utils import timezone

from birdex.models import Sighting

from .models import Note, Post


LEGACY_RSS_GUID_SLUGS = {
    "i-don-t-need-it-and-neither-do-you",
    "a-letter-of-appreciation-to-fiction-writers",
    "i-turned-off-estimated-read-time",
    "getting-away-from-the-algorithm",
    "blog-design-refinements",
    "playing-around-as-a-sysadmin",
    "goodbye-hermes",
    "don-t-be-afraid-to-have-a-voice",
    "hands-on-with-fable-5",
    "im-done-picking-a-niche",
    "just-write",
    "agentic-ai-isnt-just-coding",
    "building-with-local-models",
    "learning-with-ai",
    "gemma-4-e4b-local-coding-assistant",
    "threads-spotlight-vol-1",
    "writing-at-the-speed-of-thought-27-days-with-wispr-flow",
    "my-ai-designed-site-was-perfect-and-that-was-the-problem",
    "design-breakdown-hero-accounting-firm",
}


def site_url(path):
    return f"{settings.SITE_URL}{path}"


class FullContentRssFeed(Rss201rev2Feed):
    """RSS 2.0 with widely supported full-content and media extensions."""

    def rss_attributes(self):
        attributes = super().rss_attributes()
        attributes.update(
            {
                "xmlns:content": "http://purl.org/rss/1.0/modules/content/",
                "xmlns:media": "http://search.yahoo.com/mrss/",
            }
        )
        return attributes

    def add_item_elements(self, handler, item):
        super().add_item_elements(handler, item)
        if item.get("content") is not None:
            handler.addQuickElement("content:encoded", item["content"])
        if item.get("media_url"):
            handler.addQuickElement(
                "media:content",
                "",
                {"url": item["media_url"], "medium": "image"},
            )


class LatestPostsFeed(Feed):
    feed_type = FullContentRssFeed
    title = "Antonio Santos"
    description = "Latest posts, notes, and bird sightings"

    def link(self):
        return site_url("/")

    def feed_url(self):
        return site_url("/rss.xml")

    def items(self):
        posts = Post.objects.filter(
            status=Post.PUBLISHED, published_at__lte=timezone.now()
        )
        notes = Note.objects.filter(
            status=Note.PUBLISHED, published_at__lte=timezone.now()
        )
        sightings = Sighting.objects.published().with_primary_photo()
        return sorted(
            [*posts, *notes, *sightings],
            key=lambda item: item.published_at,
            reverse=True,
        )[:20]

    def item_title(self, item):
        if isinstance(item, Note):
            return item.display_title
        if isinstance(item, Sighting):
            return f"{item.bird.common_name} sighting"
        return item.title

    def item_description(self, item):
        if isinstance(item, Sighting):
            return render_to_string(
                "birdex/includes/sighting_feed.html",
                {
                    "sighting": item,
                    "sighting_url": self.item_link(item),
                },
            ).strip()
        return item.body_html

    def item_extra_kwargs(self, item):
        return {
            "content": self.item_description(item),
            "media_url": (
                item.primary_photo.image.url
                if isinstance(item, Sighting) and item.primary_photo
                else None
            ),
        }

    def item_link(self, item):
        return site_url(item.get_absolute_url())

    def item_guid(self, item):
        if isinstance(item, Post) and item.slug in LEGACY_RSS_GUID_SLUGS:
            return site_url(f"/posts/{item.slug}/")
        return self.item_link(item)

    def item_guid_is_permalink(self, item):
        return not isinstance(item, Post) or item.slug not in LEGACY_RSS_GUID_SLUGS

    def item_pubdate(self, item):
        return item.published_at


class PostsOnlyFeed(LatestPostsFeed):
    title = "Antonio Santos — Posts"
    description = "Latest posts"

    def feed_url(self):
        return site_url("/posts.rss.xml")

    def items(self):
        return Post.objects.filter(
            status=Post.PUBLISHED,
            published_at__lte=timezone.now(),
        )
