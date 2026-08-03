from django.urls import path
from django.contrib.sitemaps.views import sitemap
from django.contrib import admin

from .feeds import LatestPostsFeed, PostsOnlyFeed
from . import views
from .sitemaps import PostSitemap, StaticViewSitemap

app_name = "blog"

admin.site.site_header = "Antonio's Studio"
admin.site.index_title = "Publishing desk"
admin.site.site_title = "Antonio's Studio"

sitemaps = {
    "pages": StaticViewSitemap,
    "posts": PostSitemap,
}

urlpatterns = [
    path("", views.home, name="home"),
    path("archive/", views.archive, name="archive"),
    path("notes/", views.notes, name="notes"),
    path("now/", views.now, name="now"),
    path("blogroll/", views.blogroll, name="blogroll"),
    path("post/<slug:slug>/", views.legacy_post_redirect, name="legacy_post_detail"),
    path("posts/<slug:slug>/", views.legacy_post_redirect, name="legacy_posts_detail"),
    path("rss.xml", LatestPostsFeed(), name="rss"),
    path("posts.rss.xml", PostsOnlyFeed(), name="posts_rss"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path("media/<slug:slug>/", views.media_detail, name="media_detail"),
    path("notes/<slug:slug>/", views.note_detail, name="note_detail"),
    path("subscribe.html", views.subscribe, name="subscribe"),
    path("subscribe/", views.legacy_subscribe_redirect, name="legacy_subscribe"),
    path("tags/<slug:slug>/", views.tag_detail, name="tag_detail"),
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
