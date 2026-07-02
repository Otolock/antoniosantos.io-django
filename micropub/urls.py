from django.urls import path

from . import views


app_name = "micropub"

urlpatterns = [
    path("micropub", views.micropub, name="endpoint_no_slash"),
    path("micropub/", views.micropub, name="endpoint"),
    path("micropub/media", views.media, name="media_endpoint_no_slash"),
    path("micropub/media/", views.media, name="media_endpoint"),
]
