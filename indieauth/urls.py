from django.urls import path

from . import views


app_name = "indieauth"

urlpatterns = [
    path(".well-known/oauth-authorization-server", views.metadata, name="metadata"),
    path("indieauth/auth", views.auth, name="auth"),
    path("indieauth/token", views.token, name="token"),
    path("indieauth/introspect", views.introspect, name="introspect"),
    path("indieauth/revoke", views.revoke, name="revoke"),
]
