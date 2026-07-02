import base64
import hashlib
from unittest import mock

from django.test import TestCase, override_settings, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model

from indieauth.models import AccessToken, AuthCode
from indieauth import utils


TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


@override_settings(SITE_URL="https://antoniosantos.io")
class MetadataTests(TestCase):
    def test_metadata_returns_required_fields(self):
        response = self.client.get(reverse("indieauth:metadata"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["issuer"], "https://antoniosantos.io/")
        self.assertTrue(data["authorization_endpoint"].endswith(reverse("indieauth:auth")))
        self.assertTrue(data["token_endpoint"].endswith(reverse("indieauth:token")))
        self.assertIn("introspection_endpoint", data)
        self.assertIn("revocation_endpoint", data)
        self.assertIn("S256", data["code_challenge_methods_supported"])
        self.assertIn("create", data["scopes_supported"])
        self.assertTrue(data["authorization_response_iss_parameter_supported"])


def _pkce_pair(method="S256"):
    verifier = "a" * 48
    if method == "S256":
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
    else:
        challenge = verifier
    return verifier, challenge


@override_settings(SITE_URL="https://antoniosantos.io", STORAGES=TEST_STORAGES)
class AuthorizationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="tony", password="secret", is_staff=True
        )
        self.client.login(username="tony", password="secret")
        self.verifier, self.challenge = _pkce_pair("S256")
        self.params = {
            "response_type": "code",
            "client_id": "http://127.0.0.1:8000/",
            "redirect_uri": "http://127.0.0.1:8000/callback",
            "state": "abc123",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
            "scope": "create",
            "me": "https://antoniosantos.io/",
        }

    def test_unauthenticated_user_is_redirected_to_admin_login(self):
        self.client.logout()
        response = self.client.get(reverse("indieauth:auth"), self.params)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])

    def test_unauthenticated_post_does_not_issue_code(self):
        self.client.logout()
        response = self.client.post(
            reverse("indieauth:auth"), dict(self.params, action="approve")
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])
        self.assertEqual(AuthCode.objects.count(), 0)

    def test_loopback_client_not_fetched(self):
        with mock.patch("indieauth.utils.requests.get") as fake_get:
            response = self.client.get(reverse("indieauth:auth"), self.params)
            fake_get.assert_not_called()
        self.assertEqual(response.status_code, 200)

    def test_rejects_unsupported_response_type(self):
        self.params["response_type"] = "token"
        response = self.client.get(reverse("indieauth:auth"), self.params)
        # redirect_uri is valid, so the error is sent back via redirect.
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=unsupported_response_type", response["Location"])

    def test_rejects_redirect_uri_not_matching_client(self):
        self.params["redirect_uri"] = "https://evil.example/callback"
        with mock.patch(
            "indieauth.utils.fetch_client_metadata",
            return_value={
                "client_id": self.params["client_id"],
                "client_name": "test",
                "redirect_uris": [],
            },
        ):
            response = self.client.get(reverse("indieauth:auth"), self.params)
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_http_client_id(self):
        self.params["client_id"] = "javascript:alert(1)"
        response = self.client.get(reverse("indieauth:auth"), self.params)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AuthCode.objects.count(), 0)

    def test_scoped_request_requires_pkce(self):
        del self.params["code_challenge"]
        del self.params["code_challenge_method"]
        response = self.client.get(reverse("indieauth:auth"), self.params)
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=invalid_request", response["Location"])
        self.assertEqual(AuthCode.objects.count(), 0)

    def test_scoped_request_requires_s256_pkce(self):
        self.params["code_challenge_method"] = "plain"
        response = self.client.get(reverse("indieauth:auth"), self.params)
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=invalid_request", response["Location"])
        self.assertEqual(AuthCode.objects.count(), 0)

    def test_approve_issues_code_and_redirects_with_iss(self):
        post_params = dict(self.params, action="approve")
        response = self.client.post(reverse("indieauth:auth"), post_params)
        self.assertEqual(response.status_code, 302)
        self.assertIn("http://127.0.0.1:8000/callback", response["Location"])
        self.assertIn("code=", response["Location"])
        self.assertIn("state=abc123", response["Location"])
        self.assertIn("iss=", response["Location"])
        self.assertEqual(AuthCode.objects.count(), 1)
        code = AuthCode.objects.get()
        self.assertEqual(code.scope, "create")
        self.assertEqual(code.code_challenge, self.challenge)
        self.assertFalse(code.used)

    def test_deny_redirects_with_access_denied(self):
        post_params = dict(self.params, action="deny")
        response = self.client.post(reverse("indieauth:auth"), post_params)
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=access_denied", response["Location"])
        self.assertEqual(AuthCode.objects.count(), 0)


@override_settings(SITE_URL="https://antoniosantos.io")
class TokenTests(TestCase):
    def setUp(self):
        self.verifier, self.challenge = _pkce_pair("S256")
        self.code = AuthCode.objects.create(
            code="test-code",
            client_id="http://127.0.0.1:8000/",
            redirect_uri="http://127.0.0.1:8000/callback",
            code_challenge=self.challenge,
            code_challenge_method="S256",
            scope="create",
            me="https://antoniosantos.io/",
        )

    def test_exchange_code_for_access_token(self):
        response = self.client.post(
            reverse("indieauth:token"),
            {
                "grant_type": "authorization_code",
                "code": "test-code",
                "client_id": "http://127.0.0.1:8000/",
                "redirect_uri": "http://127.0.0.1:8000/callback",
                "code_verifier": self.verifier,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "Bearer")
        self.assertEqual(data["scope"], "create")
        self.assertEqual(data["me"], "https://antoniosantos.io/")
        self.assertTrue(AccessToken.objects.filter(token=data["access_token"]).exists())
        self.code.refresh_from_db()
        self.assertTrue(self.code.used)

    def test_code_is_single_use(self):
        payload = {
            "grant_type": "authorization_code",
            "code": "test-code",
            "client_id": "http://127.0.0.1:8000/",
            "redirect_uri": "http://127.0.0.1:8000/callback",
            "code_verifier": self.verifier,
        }
        self.client.post(reverse("indieauth:token"), payload)
        response = self.client.post(reverse("indieauth:token"), payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_pkce_failure_rejects(self):
        response = self.client.post(
            reverse("indieauth:token"),
            {
                "grant_type": "authorization_code",
                "code": "test-code",
                "client_id": "http://127.0.0.1:8000/",
                "redirect_uri": "http://127.0.0.1:8000/callback",
                "code_verifier": "wrong-verifier",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")
        # Code not marked used on failure.
        self.code.refresh_from_db()
        self.assertFalse(self.code.used)

    def test_unknown_code_rejected(self):
        response = self.client.post(
            reverse("indieauth:token"),
            {
                "grant_type": "authorization_code",
                "code": "does-not-exist",
                "client_id": "http://127.0.0.1:8000/",
                "redirect_uri": "http://127.0.0.1:8000/callback",
                "code_verifier": self.verifier,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_no_scope_does_not_issue_token(self):
        self.code.scope = ""
        self.code.save(update_fields=["scope"])
        response = self.client.post(
            reverse("indieauth:token"),
            {
                "grant_type": "authorization_code",
                "code": "test-code",
                "client_id": "http://127.0.0.1:8000/",
                "redirect_uri": "http://127.0.0.1:8000/callback",
                "code_verifier": self.verifier,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_scope")

    def test_unsupported_grant_type(self):
        response = self.client.post(
            reverse("indieauth:token"),
            {"grant_type": "refresh_token", "refresh_token": "x"},
        )
        self.assertEqual(response.status_code, 400)


@override_settings(SITE_URL="https://antoniosantos.io")
class IntrospectionAndRevocationTests(TestCase):
    def setUp(self):
        self.token = AccessToken.objects.create(
            token="active-token",
            client_id="http://127.0.0.1:8000/",
            scope="create update",
            me="https://antoniosantos.io/",
        )

    def test_introspect_active_token(self):
        response = self.client.post(reverse("indieauth:introspect"), {"token": "active-token"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIs(data["active"], True)
        self.assertEqual(data["me"], "https://antoniosantos.io/")
        self.assertEqual(data["scope"], "create update")

    def test_introspect_unknown_token(self):
        response = self.client.post(reverse("indieauth:introspect"), {"token": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["active"], False)

    def test_introspect_revoked_token(self):
        self.token.revoked = True
        self.token.save(update_fields=["revoked"])
        response = self.client.post(reverse("indieauth:introspect"), {"token": "active-token"})
        self.assertIs(response.json()["active"], False)

    def test_revoke_marks_token_inactive(self):
        response = self.client.post(reverse("indieauth:revoke"), {"token": "active-token"})
        self.assertEqual(response.status_code, 200)
        self.token.refresh_from_db()
        self.assertTrue(self.token.revoked)

    def test_revoke_unknown_token_returns_200(self):
        response = self.client.post(reverse("indieauth:revoke"), {"token": "nope"})
        self.assertEqual(response.status_code, 200)


class UtilsTests(TestCase):
    def test_canonicalize_adds_path(self):
        self.assertEqual(utils.canonicalize_url("https://example.com"), "https://example.com/")

    def test_canonicalize_lowercases_host(self):
        self.assertEqual(
            utils.canonicalize_url("HTTPS://Example.COM/Foo"),
            "https://example.com/Foo",
        )

    def test_is_loopback_client(self):
        self.assertTrue(utils.is_loopback_client("http://127.0.0.1:8000/"))
        self.assertTrue(utils.is_loopback_client("http://localhost:8000/"))
        self.assertFalse(utils.is_loopback_client("https://app.example.com/"))

    def test_is_safe_fetch_url_rejects_private_dns_results(self):
        with mock.patch(
            "indieauth.utils.socket.getaddrinfo",
            return_value=[
                (
                    utils.socket.AF_INET,
                    utils.socket.SOCK_STREAM,
                    6,
                    "",
                    ("10.0.0.5", 443),
                )
            ],
        ):
            self.assertFalse(utils.is_safe_fetch_url("https://client.example/"))

    def test_is_safe_fetch_url_rejects_invalid_ports(self):
        self.assertFalse(utils.is_safe_fetch_url("https://client.example:bad/"))

    def test_fetch_client_metadata_rejects_redirect_to_private_host(self):
        response = mock.Mock()
        response.is_redirect = True
        response.headers = {"Location": "http://127.0.0.1:8000/private"}

        with (
            mock.patch(
                "indieauth.utils.socket.getaddrinfo",
                return_value=[
                    (
                        utils.socket.AF_INET,
                        utils.socket.SOCK_STREAM,
                        6,
                        "",
                        ("93.184.216.34", 443),
                    )
                ],
            ),
            mock.patch("indieauth.utils.requests.get", return_value=response) as fake_get,
        ):
            self.assertIsNone(utils.fetch_client_metadata("https://client.example/"))
            fake_get.assert_called_once()

    def test_verify_pkce_s256(self):
        verifier, challenge = _pkce_pair("S256")
        self.assertTrue(utils.verify_pkce(verifier, challenge, "S256"))
        self.assertFalse(utils.verify_pkce("wrong", challenge, "S256"))

    def test_verify_pkce_plain(self):
        verifier, challenge = _pkce_pair("plain")
        self.assertTrue(utils.verify_pkce(verifier, challenge, "plain"))

    def test_redirect_uri_same_host_allowed(self):
        meta = {"redirect_uris": []}
        self.assertTrue(
            utils.redirect_uri_allowed(
                "https://app.example.com/",
                "https://app.example.com/callback",
                meta,
            )
        )

    def test_redirect_uri_mismatch_blocked(self):
        meta = {"redirect_uris": []}
        self.assertFalse(
            utils.redirect_uri_allowed(
                "https://app.example.com/",
                "https://evil.example/callback",
                meta,
            )
        )

    def test_redirect_uri_published_allowed(self):
        meta = {"redirect_uris": ["https://other.example/cb"]}
        self.assertTrue(
            utils.redirect_uri_allowed(
                "https://app.example.com/",
                "https://other.example/cb",
                meta,
            )
        )
