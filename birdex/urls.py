from django.urls import path

from . import views


app_name = "birdex"

urlpatterns = [
    path("", views.home, name="home"),
    path(
        "<slug:bird_slug>/sightings/<int:pk>/",
        views.sighting_detail,
        name="sighting_detail",
    ),
    path("<slug:slug>/", views.bird_detail, name="bird_detail"),
]
