# webmentions/urls.py

from django.urls import path
from . import views

app_name = "webmentions"

urlpatterns = [
    path("", views.receive_webmention, name="receive"),
]
