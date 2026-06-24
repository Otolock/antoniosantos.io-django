from django.contrib.syndication.views import Feed
from django.utils import timezone

from .models import Post


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


class LatestPostsFeed(Feed):
    title = "Antonio Santos"
    link = "/"
    description = "Latest posts"

    def items(self):
        return Post.objects.filter(
            status=Post.PUBLISHED,
            published_at__lte=timezone.now(),
        )[:20]

    def get_feed(self, obj, request):
        posts = list(self.items())
        feed = super().get_feed(obj, request)

        for feed_item, post in zip(feed.items, posts):
            feed_item["unique_id_is_permalink"] = True
            if post.slug in LEGACY_RSS_GUID_SLUGS:
                feed_item["unique_id"] = request.build_absolute_uri(
                    f"/posts/{post.slug}/"
                )

        return feed

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.description

    def item_link(self, item):
        return item.get_absolute_url()
