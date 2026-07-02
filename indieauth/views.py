"""IndieAuth server: metadata, authorization, token, introspection, revocation."""

from urllib.parse import urlencode, quote, urlsplit

from django.conf import settings
from django.db import transaction
from django.http import (
    JsonResponse,
    HttpResponseRedirect,
)
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods

from .models import AccessToken, AuthCode
from .utils import (
    canonicalize_url,
    fetch_client_metadata,
    redirect_uri_allowed,
    verify_pkce,
)


def _site_me():
    return (getattr(settings, "INDIEAUTH_ME", "") or settings.SITE_URL.rstrip("/") + "/").rstrip("/") + "/"


def _issuer():
    return settings.SITE_URL.rstrip("/") + "/"


# ---------------------------------------------------------------------------
# Server metadata (RFC 8414, served at .well-known/oauth-authorization-server)
# ---------------------------------------------------------------------------


def metadata(request):
    base = settings.SITE_URL.rstrip("/")
    return JsonResponse(
        {
            "issuer": _issuer(),
            "authorization_endpoint": f"{base}{reverse('indieauth:auth')}",
            "token_endpoint": f"{base}{reverse('indieauth:token')}",
            "introspection_endpoint": f"{base}{reverse('indieauth:introspect')}",
            "introspection_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint": f"{base}{reverse('indieauth:revoke')}",
            "revocation_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["profile", "email", "create", "update", "delete"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256", "plain"],
            "authorization_response_iss_parameter_supported": True,
            "service_documentation": "https://indieauth.spec.indieweb.org/",
        }
    )


# ---------------------------------------------------------------------------
# Authorization endpoint
# ---------------------------------------------------------------------------


AUTH_PARAMS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "state",
    "code_challenge",
    "code_challenge_method",
    "scope",
    "me",
)


def _collect_auth_params(request):
    source = request.GET if request.method == "GET" else request.POST
    return {key: source.get(key, "") for key in AUTH_PARAMS}


def _validate_auth_request(params):
    """Return (client_metadata, error_response). error_response is None on success."""
    if params["response_type"] and params["response_type"] != "code":
        return None, {"error": "unsupported_response_type", "error_description": "Only response_type=code is supported."}

    if not params["client_id"] or not params["redirect_uri"]:
        return None, {"error": "invalid_request", "error_description": "client_id and redirect_uri are required."}

    if params["scope"]:
        if not params["code_challenge"]:
            return None, {"error": "invalid_request", "error_description": "Scoped authorization requests require PKCE."}
        if (params["code_challenge_method"] or "S256") not in {"S256", "plain"}:
            return None, {"error": "invalid_request", "error_description": "Unsupported PKCE method."}

    # Canonicalize for comparison but keep originals as sent.
    client_id = canonicalize_url(params["client_id"])
    redirect_uri = canonicalize_url(params["redirect_uri"])

    if not _is_http_url(client_id) or not _is_redirect_url(redirect_uri):
        return None, {
            "error": "invalid_request",
            "error_description": "client_id must be an HTTP(S) URL and redirect_uri must be a valid callback URL.",
        }

    client_metadata = fetch_client_metadata(client_id)
    # For loopback clients the metadata is synthesized and never None.

    if not redirect_uri_allowed(client_id, redirect_uri, client_metadata):
        return None, {
            "error": "invalid_request",
            "error_description": "redirect_uri does not match the client_id or its registered redirect URIs.",
        }

    return client_metadata, None


@csrf_exempt
@require_http_methods(["GET", "POST"])
def auth(request):
    if request.method == "POST" and request.POST.get("grant_type") == "authorization_code":
        return _exchange_profile_code(request)
    return _auth_consent(request)


@csrf_protect
def _auth_consent(request):
    params = _collect_auth_params(request)

    if not request.user.is_authenticated:
        return HttpResponseRedirect(
            f"{reverse('admin:login')}?next={quote(request.get_full_path())}"
        )

    client_metadata, error = _validate_auth_request(params)
    if error:
        # If we can't even validate the redirect_uri we must not redirect back;
        # show the error to the user instead.
        if not redirect_uri_allowed(
            canonicalize_url(params["client_id"]),
            canonicalize_url(params["redirect_uri"]),
            client_metadata,
        ):
            return _render_error(request, "Invalid request", error["error_description"])
        return _redirect_with_error(params["redirect_uri"], error["error"], params.get("state", ""))

    if request.method == "GET":
        return TemplateResponse(
            request,
            "indieauth/consent.html",
            {
                "client_metadata": client_metadata,
                "client_id": params["client_id"],
                "redirect_uri": params["redirect_uri"],
                "scope": params["scope"],
                "scopes": params["scope"].split(),
                "params": params,
            },
        )

    # POST: user approved or denied the consent form.
    if request.POST.get("action") == "deny":
        return _redirect_with_error(params["redirect_uri"], "access_denied", params.get("state", ""))

    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method") or "S256"
    if code_challenge and code_challenge_method not in {"S256", "plain"}:
        return _redirect_with_error(
            params["redirect_uri"], "invalid_request", params.get("state", "")
        )

    code = AuthCode.generate()
    AuthCode.objects.create(
        code=code,
        client_id=canonicalize_url(params["client_id"]),
        redirect_uri=canonicalize_url(params["redirect_uri"]),
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method if code_challenge else "S256",
        scope=params.get("scope", ""),
        me=_site_me(),
    )

    redirect = canonicalize_url(params["redirect_uri"])
    query = urlencode(
        {
            "code": code,
            "state": params.get("state", ""),
            "iss": _issuer(),
        }
    )
    separator = "&" if "?" in redirect else "?"
    return HttpResponseRedirect(f"{redirect}{separator}{query}")


# ---------------------------------------------------------------------------
# Token endpoint (authorization code exchange, introspection, revocation)
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def token(request):
    grant_type = request.POST.get("grant_type", "")

    if grant_type == "authorization_code":
        return _exchange_code(request)
    if grant_type == "refresh_token":
        # Long-lived tokens: refresh is not supported.
        return _token_error("invalid_grant", "Refresh tokens are not issued.")

    return _token_error("unsupported_grant_type", "Only authorization_code is supported.")


def _exchange_code(request):
    code = request.POST.get("code", "")
    client_id = canonicalize_url(request.POST.get("client_id", ""))
    redirect_uri = canonicalize_url(request.POST.get("redirect_uri", ""))

    if not code or not client_id or not redirect_uri:
        return _token_error("invalid_request", "code, client_id and redirect_uri are required.")

    auth_code, error = _validate_code_exchange(request, client_id, redirect_uri)
    if error:
        return error
    if not auth_code.scope:
        # Per spec, the token endpoint MUST NOT issue an access token when no
        # scope was requested; the code should have been redeemed at the auth
        # endpoint for a profile URL only.
        return _token_error("invalid_scope", "No scope was requested; cannot issue an access token.")

    error = _mark_code_used(auth_code)
    if error:
        return error

    token = AccessToken.generate()
    access_token = AccessToken.objects.create(
        token=token,
        client_id=client_id,
        scope=auth_code.scope,
        me=auth_code.me,
    )

    return JsonResponse(
        {
            "access_token": access_token.token,
            "token_type": "Bearer",
            "scope": access_token.scope,
            "me": access_token.me,
        }
    )


def _exchange_profile_code(request):
    code = request.POST.get("code", "")
    client_id = canonicalize_url(request.POST.get("client_id", ""))
    redirect_uri = canonicalize_url(request.POST.get("redirect_uri", ""))

    if not code or not client_id or not redirect_uri:
        return _token_error("invalid_request", "code, client_id and redirect_uri are required.")

    auth_code, error = _validate_code_exchange(request, client_id, redirect_uri)
    if error:
        return error

    error = _mark_code_used(auth_code)
    if error:
        return error

    return JsonResponse({"me": auth_code.me})


def _validate_code_exchange(request, client_id, redirect_uri):
    code = request.POST.get("code", "")
    code_verifier = request.POST.get("code_verifier", "")

    try:
        auth_code = AuthCode.objects.get(code=code)
    except AuthCode.DoesNotExist:
        return None, _token_error("invalid_grant", "Authorization code not found.")

    if auth_code.used:
        return None, _token_error("invalid_grant", "Authorization code already used.")
    if auth_code.is_expired():
        auth_code.delete()
        return None, _token_error("invalid_grant", "Authorization code expired.")
    if auth_code.client_id != client_id:
        return None, _token_error("invalid_grant", "client_id does not match.")
    if auth_code.redirect_uri != redirect_uri:
        return None, _token_error("invalid_grant", "redirect_uri does not match.")
    if not verify_pkce(code_verifier, auth_code.code_challenge, auth_code.code_challenge_method):
        return None, _token_error("invalid_grant", "PKCE verification failed.")

    return auth_code, None


def _mark_code_used(auth_code):
    with transaction.atomic():
        updated = AuthCode.objects.filter(pk=auth_code.pk, used=False).update(used=True)
        if updated == 0:
            return _token_error("invalid_grant", "Authorization code already used.")
    return None


@csrf_exempt
@require_http_methods(["POST"])
def introspect(request):
    token = request.POST.get("token", "")
    if not token:
        return JsonResponse({"active": False})

    try:
        access_token = AccessToken.objects.get(token=token)
    except AccessToken.DoesNotExist:
        return JsonResponse({"active": False})

    if not access_token.is_active:
        return JsonResponse({"active": False})

    return JsonResponse(
        {
            "active": True,
            "me": access_token.me,
            "client_id": access_token.client_id,
            "scope": access_token.scope,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def revoke(request):
    token = request.POST.get("token", "")
    if token:
        AccessToken.objects.filter(token=token).update(revoked=True)
    # RFC 7009: always return 200, even for unknown tokens.
    return JsonResponse({})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _oauth_error(error, description, status=400):
    return JsonResponse(
        {"error": error, "error_description": description},
        status=status,
    )


def _token_error(error, description, status=400):
    return _oauth_error(error, description, status=status)


def _redirect_with_error(redirect_uri, error, state):
    redirect = canonicalize_url(redirect_uri)
    query = urlencode({"error": error, "state": state})
    separator = "&" if "?" in redirect else "?"
    return HttpResponseRedirect(f"{redirect}{separator}{query}")


def _render_error(request, title, description):
    return TemplateResponse(
        request,
        "indieauth/error.html",
        {"title": title, "description": description},
        status=400,
    )


def _is_http_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    try:
        parts.port
    except ValueError:
        return False
    return True


def _is_redirect_url(url):
    parts = urlsplit(url)
    if not parts.scheme or parts.scheme.lower() in {"data", "javascript"}:
        return False
    try:
        parts.port
    except ValueError:
        return False
    return True
