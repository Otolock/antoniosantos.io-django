from django.urls import path

from .feeds import LatestPostsFeed
from . import views

app_name = "blog"

urlpatterns = [
    path("", views.home, name="home"),
    path("archive/", views.archive, name="archive"),
    path("now/", views.now, name="now"),
    path("rss.xml", LatestPostsFeed(), name="rss"),
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
