from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from .models import Note, Post


class StaticViewSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return [
            "blog:home",
            "blog:now",
            "blog:archive",
            "blog:notes",
            "blog:subscribe",
        ]

    def location(self, item):
        return reverse(item)


class PostSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        posts = Post.objects.filter(
            status=Post.PUBLISHED,
            published_at__lte=timezone.now(),
        )
        notes = Note.objects.filter(
            status=Note.PUBLISHED,
            published_at__lte=timezone.now(),
        )
        return sorted([*posts, *notes], key=lambda item: item.published_at, reverse=True)

    def lastmod(self, post):
        return post.published_at
