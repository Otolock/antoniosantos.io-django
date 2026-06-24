from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.utils import timezone

from .models import Post


class LatestPostsFeed(Feed):
    title = "Antonio Santos"
    link = "/"
    description = "Latest posts"

    def items(self):
        return Post.objects.filter(
            status=Post.PUBLISHED,
            published_at__lte=timezone.now(),
        )[:20]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.description

    def item_link(self, item):
        return reverse(
            "blog:post_detail",
            args=[item.slug],
        )
