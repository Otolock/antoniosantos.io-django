from django.urls import path
from django.contrib.sitemaps.views import sitemap

from .feeds import LatestPostsFeed
from . import views
from .sitemaps import PostSitemap, StaticViewSitemap

app_name = "blog"

sitemaps = {
    "pages": StaticViewSitemap,
    "posts": PostSitemap,
}

urlpatterns = [
    path("", views.home, name="home"),
    path("archive/", views.archive, name="archive"),
    path("now/", views.now, name="now"),
    path("post/<slug:slug>/", views.legacy_post_redirect, name="legacy_post_detail"),
    path("posts/<slug:slug>/", views.legacy_post_redirect, name="legacy_posts_detail"),
    path("rss.xml", LatestPostsFeed(), name="rss"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path("media/<slug:slug>/", views.media_detail, name="media_detail"),
    path("micropub", views.micropub, name="micropub_no_slash"),
    path("micropub/", views.micropub, name="micropub"),
    path("subscribe/", views.subscribe, name="subscribe"),
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
