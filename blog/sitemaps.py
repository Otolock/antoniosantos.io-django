from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from .models import Post


class StaticViewSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return ["blog:home", "blog:now", "blog:archive", "blog:subscribe"]

    def location(self, item):
        return reverse(item)


class PostSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Post.objects.filter(
            status=Post.PUBLISHED,
            published_at__lte=timezone.now(),
        )

    def lastmod(self, post):
        return post.published_at
