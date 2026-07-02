import secrets

from django.db import models
from django.utils import timezone


# Authorization codes are short-lived and single-use (spec recommends
# a maximum lifetime of 10 minutes).
AUTH_CODE_LIFETIME_SECONDS = 600


class AuthCode(models.Model):
    code = models.CharField(max_length=128, unique=True)
    client_id = models.URLField(max_length=500)
    redirect_uri = models.URLField(max_length=500)
    code_challenge = models.CharField(max_length=256, blank=True)
    code_challenge_method = models.CharField(max_length=16, default="S256")
    scope = models.CharField(max_length=300, blank=True)
    me = models.URLField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    def is_expired(self):
        return timezone.now() > self.created_at + timezone.timedelta(
            seconds=AUTH_CODE_LIFETIME_SECONDS
        )

    @classmethod
    def generate(cls):
        return secrets.token_urlsafe(32)


class AccessToken(models.Model):
    token = models.CharField(max_length=128, unique=True)
    client_id = models.URLField(max_length=500, blank=True)
    scope = models.CharField(max_length=300, blank=True)
    me = models.URLField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked = models.BooleanField(default=False)

    @property
    def is_active(self):
        return not self.revoked

    @property
    def scopes(self):
        return [s for s in self.scope.split() if s]

    @classmethod
    def generate(cls):
        return secrets.token_urlsafe(32)
