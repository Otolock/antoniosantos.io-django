from django.urls import path

from . import views


app_name = "micropub"

urlpatterns = [
    path("micropub", views.micropub, name="endpoint_no_slash"),
    path("micropub/", views.micropub, name="endpoint"),
]
