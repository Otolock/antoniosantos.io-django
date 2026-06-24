from django.urls import path

from .feeds import LatestPostsFeed
from . import views

app_name = "blog"

urlpatterns = [
    path("", views.post_list, name="post_list"),
    path("rss.xml", LatestPostsFeed(), name="rss"),
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]