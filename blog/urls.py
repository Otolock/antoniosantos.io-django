from django.urls import path

from .feeds import LatestPostsFeed
from . import views

app_name = "blog"

urlpatterns = [
    path("", views.home, name="home"),
    path("archive/", views.archive, name="archive"),
    path("now/", views.now, name="now"),
    path("post/<slug:slug>/", views.legacy_post_redirect, name="legacy_post_detail"),
    path("posts/<slug:slug>/", views.legacy_post_redirect, name="legacy_posts_detail"),
    path("rss.xml", LatestPostsFeed(), name="rss"),
    path("subscribe/", views.subscribe, name="subscribe"),
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
