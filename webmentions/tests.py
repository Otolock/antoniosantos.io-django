from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from blog.models import Post

from .models import SentWebmention, Webmention
from .services import (
    SourceVerificationResult,
    WebmentionValidationError,
    discover_webmention_endpoint,
    extract_webmention_targets,
    queue_webmentions_for_post,
    send_webmention,
    send_webmentions_for_post,
    send_webmentions_for_post_async,
    verify_source_links_to_target,
)

TEST_STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    }
}


@override_settings(STORAGES=TEST_STORAGES)
class WebmentionAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.user)

    def test_received_list_is_a_scannable_inbox(self):
        Webmention.objects.create(
            source_url="https://mentioner.example/reply/",
            target_url="https://example.com/live-post/",
            title="A thoughtful reply",
            author_name="Mentioner",
            content="This is a useful contribution to the conversation.",
        )

        response = self.client.get(reverse("admin:webmentions_webmention_changelist"))

        self.assertContains(response, "Webmention inbox")
        self.assertContains(response, "A thoughtful reply")
        self.assertContains(response, "Mentioner")
        self.assertContains(response, "Open source")
        self.assertContains(response, "Approve")
        self.assertContains(response, "webmention-stat-tabs")
        self.assertContains(response, "Add webmention")

    def test_admin_can_add_a_webmention_manually(self):
        add_url = reverse("admin:webmentions_webmention_add")

        response = self.client.get(add_url)

        self.assertContains(response, "Source and destination")
        self.assertContains(response, 'name="source_url"')
        self.assertContains(response, 'name="target_url"')

        response = self.client.post(
            add_url,
            {
                "source_url": "https://manual.example/a-reply/",
                "target_url": "https://example.com/live-post/",
                "author_name": "Manual Author",
                "title": "A manually entered reply",
                "content": "Added from the admin.",
                "status": Webmention.APPROVED,
                "_save": "Save",
            },
        )

        self.assertRedirects(
            response,
            reverse("admin:webmentions_webmention_changelist"),
        )
        mention = Webmention.objects.get(source_url="https://manual.example/a-reply/")
        self.assertEqual(mention.status, Webmention.APPROVED)
        self.assertEqual(mention.author_name, "Manual Author")

    def test_existing_webmention_urls_remain_read_only(self):
        mention = Webmention.objects.create(
            source_url="https://mentioner.example/original/",
            target_url="https://example.com/original-post/",
        )

        response = self.client.get(
            reverse("admin:webmentions_webmention_change", args=[mention.pk])
        )

        self.assertContains(response, mention.source_url)
        self.assertContains(response, mention.target_url)
        self.assertNotContains(response, 'name="source_url"')
        self.assertNotContains(response, 'name="target_url"')

    def test_quick_moderation_updates_status(self):
        mention = Webmention.objects.create(
            source_url="https://mentioner.example/reply/",
            target_url="https://example.com/live-post/",
        )
        url = reverse(
            "admin:webmentions_webmention_moderate",
            args=[mention.pk, "approve"],
        )

        response = self.client.post(url)

        self.assertRedirects(
            response,
            reverse("admin:webmentions_webmention_changelist"),
        )
        mention.refresh_from_db()
        self.assertEqual(mention.status, Webmention.APPROVED)

    def test_quick_moderation_requires_post(self):
        mention = Webmention.objects.create(
            source_url="https://mentioner.example/reply/",
            target_url="https://example.com/live-post/",
        )

        response = self.client.get(
            reverse(
                "admin:webmentions_webmention_moderate",
                args=[mention.pk, "spam"],
            )
        )

        self.assertEqual(response.status_code, 405)
        mention.refresh_from_db()
        self.assertEqual(mention.status, Webmention.PENDING)

    def test_sent_list_summarizes_delivery_health(self):
        SentWebmention.objects.create(
            source_url="https://example.com/live-post/",
            target_url="https://elsewhere.example/article/",
            endpoint_url="https://elsewhere.example/webmention/",
            status=SentWebmention.SENT,
            response_code=202,
            attempts=1,
        )

        response = self.client.get(
            reverse("admin:webmentions_sentwebmention_changelist")
        )

        self.assertContains(response, "Delivery log")
        self.assertContains(response, "HTTP 202")
        self.assertContains(response, "elsewhere.example")

    def test_moderation_queue_can_mark_webmention_as_spam(self):
        mention = Webmention.objects.create(
            source_url="https://spam.example/reply/",
            target_url="https://example.com/live-post/",
        )

        response = self.client.post(
            reverse("admin:moderation_queue"),
            {
                "item_type": "webmention",
                "item_id": mention.pk,
                "action": "spam",
            },
        )

        self.assertRedirects(response, reverse("admin:moderation_queue"))
        mention.refresh_from_db()
        self.assertEqual(mention.status, Webmention.SPAM)


class ReceiveWebmentionTests(TestCase):
    def setUp(self):
        self.post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="Hello.",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )
        self.url = reverse("webmentions:receive")
        self.source = "https://source.example/reply/"
        self.target = "https://example.com/live-post/"

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.views.verify_source_links_to_target")
    def test_accepts_verified_webmention_without_csrf_token(self, verify):
        verify.return_value = SourceVerificationResult(True)
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            self.url,
            {"source": self.source, "target": self.target},
        )

        self.assertEqual(response.status_code, 200)
        mention = Webmention.objects.get(source_url=self.source, target_url=self.target)
        self.assertEqual(mention.status, Webmention.PENDING)

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.views.verify_source_links_to_target")
    def test_rejects_missing_source_or_target(self, verify):
        response = self.client.post(self.url, {"source": self.source})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Webmention.objects.exists())
        verify.assert_not_called()

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.views.verify_source_links_to_target")
    def test_rejects_same_source_and_target(self, verify):
        response = self.client.post(
            self.url,
            {"source": self.target, "target": self.target},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Webmention.objects.exists())
        verify.assert_not_called()

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.views.verify_source_links_to_target")
    def test_rejects_target_on_another_site(self, verify):
        response = self.client.post(
            self.url,
            {
                "source": self.source,
                "target": "https://other.example/live-post/",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Webmention.objects.exists())
        verify.assert_not_called()

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.views.verify_source_links_to_target")
    def test_rejects_unpublished_post_targets(self, verify):
        Post.objects.create(
            title="Draft",
            slug="draft-post",
            body="Hidden.",
            status=Post.DRAFT,
        )

        response = self.client.post(
            self.url,
            {
                "source": self.source,
                "target": "https://example.com/draft-post/",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Webmention.objects.exists())
        verify.assert_not_called()

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.views.verify_source_links_to_target")
    def test_rejects_source_that_does_not_link_to_target(self, verify):
        verify.return_value = SourceVerificationResult(False)

        response = self.client.post(
            self.url,
            {"source": self.source, "target": self.target},
        )

        self.assertEqual(response.status_code, 400)
        mention = Webmention.objects.get(source_url=self.source, target_url=self.target)
        self.assertEqual(mention.status, Webmention.REJECTED)

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.views.verify_source_links_to_target")
    def test_marks_deleted_source_as_deleted(self, verify):
        verify.return_value = SourceVerificationResult(False, source_deleted=True)

        response = self.client.post(
            self.url,
            {"source": self.source, "target": self.target},
        )

        self.assertEqual(response.status_code, 400)
        mention = Webmention.objects.get(source_url=self.source, target_url=self.target)
        self.assertEqual(mention.status, Webmention.DELETED)

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.views.verify_source_links_to_target")
    def test_rejected_fetch_error_is_recorded_for_valid_target(self, verify):
        verify.side_effect = WebmentionValidationError("Source URL could not be fetched.")

        response = self.client.post(
            self.url,
            {"source": self.source, "target": self.target},
        )

        self.assertEqual(response.status_code, 400)
        mention = Webmention.objects.get(source_url=self.source, target_url=self.target)
        self.assertEqual(mention.status, Webmention.REJECTED)


class VerifySourceLinksToTargetTests(TestCase):
    source = "https://example.com/reply/"
    target = "https://example.com/live-post/"
    public_ip = {"8.8.8.8"}

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.Session")
    def test_html_source_resolves_relative_links(self, session_class, resolve_ips):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    '<article><a href="/live-post/">the post</a></article>',
                    url=self.source,
                    headers={"Content-Type": "text/html"},
                )
            ]
        )
        session_class.return_value = session

        result = verify_source_links_to_target(self.source, self.target)

        self.assertTrue(result.links_to_target)
        self.assertEqual(session.calls[0][0], self.source)

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.Session")
    def test_plain_text_source_can_mention_target(self, session_class, resolve_ips):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    f"I replied to {self.target}",
                    url=self.source,
                    headers={"Content-Type": "text/plain"},
                )
            ]
        )
        session_class.return_value = session

        result = verify_source_links_to_target(self.source, self.target)

        self.assertTrue(result.links_to_target)

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.Session")
    def test_json_source_can_mention_target(self, session_class, resolve_ips):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    f'{{"type": ["h-entry"], "url": "{self.target}"}}',
                    url=self.source,
                    headers={"Content-Type": "application/json"},
                )
            ]
        )
        session_class.return_value = session

        result = verify_source_links_to_target(self.source, self.target)

        self.assertTrue(result.links_to_target)

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.Session")
    def test_missing_link_returns_false(self, session_class, resolve_ips):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    '<a href="https://example.com/other/">other</a>',
                    url=self.source,
                    headers={"Content-Type": "text/html"},
                )
            ]
        )
        session_class.return_value = session

        result = verify_source_links_to_target(self.source, self.target)

        self.assertFalse(result.links_to_target)

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.Session")
    def test_unsupported_content_type_returns_false(self, session_class, resolve_ips):
        response = FakeResponse(
            200,
            self.target,
            url=self.source,
            headers={"Content-Type": "image/png"},
        )
        session = FakeSession([response])
        session_class.return_value = session

        result = verify_source_links_to_target(self.source, self.target)

        self.assertFalse(result.links_to_target)
        self.assertTrue(response.closed)

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.Session")
    def test_gone_source_is_treated_as_deleted(self, session_class, resolve_ips):
        session = FakeSession([FakeResponse(410, "", url=self.source)])
        session_class.return_value = session

        result = verify_source_links_to_target(self.source, self.target)

        self.assertFalse(result.links_to_target)
        self.assertTrue(result.source_deleted)

    @patch("webmentions.services._resolve_host_ips", return_value={"127.0.0.1"})
    @patch("webmentions.services.requests.Session")
    def test_private_source_host_is_not_fetched(self, session_class, resolve_ips):
        with self.assertRaises(WebmentionValidationError):
            verify_source_links_to_target(
                "http://localhost/private/",
                self.target,
            )

        session_class.return_value.get.assert_not_called()

    @patch(
        "webmentions.services._resolve_host_ips",
        side_effect=[public_ip, public_ip, {"127.0.0.1"}],
    )
    @patch("webmentions.services.requests.Session")
    def test_redirect_to_private_host_is_not_followed(self, session_class, resolve_ips):
        session = FakeSession(
            [
                FakeResponse(
                    302,
                    "",
                    url=self.source,
                    headers={"Location": "http://127.0.0.1/admin/"},
                )
            ]
        )
        session_class.return_value = session

        with self.assertRaises(WebmentionValidationError):
            verify_source_links_to_target(self.source, self.target)

        self.assertEqual(len(session.calls), 1)


class SendWebmentionTests(TestCase):
    source = "https://example.com/live-post/"
    target = "https://target.example/article/?reply=true"
    endpoint = "https://target.example/webmention/?token=abc"
    public_ip = {"8.8.8.8"}

    def test_extract_webmention_targets_returns_unique_external_links(self):
        html = """
        <p>
            <a href="https://target.example/article/">external</a>
            <a href="https://target.example/article/">duplicate</a>
            <a href="/internal/">internal</a>
            <a href="mailto:hi@example.com">email</a>
        </p>
        """

        targets = extract_webmention_targets(html, self.source)

        self.assertEqual(targets, ["https://target.example/article/"])

    @override_settings(SITE_URL="https://example.com")
    def test_queue_webmentions_for_post_only_returns_new_targets(self):
        post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="[Elsewhere](https://target.example/article/)",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )

        records = queue_webmentions_for_post(post)
        repeated_records = queue_webmentions_for_post(post)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].target_url, "https://target.example/article/")
        self.assertEqual(repeated_records, [])
        self.assertEqual(SentWebmention.objects.count(), 1)

    @override_settings(SITE_URL="https://example.com")
    def test_queue_webmentions_for_post_includes_reply_to_url(self):
        post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="A reply with no body links.",
            reply_to_url="https://target.example/article/",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )

        records = queue_webmentions_for_post(post)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].target_url, "https://target.example/article/")

    @override_settings(SITE_URL="https://example.com")
    def test_queue_webmentions_for_post_deduplicates_reply_to_url_from_body(self):
        post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="[Elsewhere](https://target.example/article/)",
            reply_to_url="https://target.example/article/",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )

        records = queue_webmentions_for_post(post)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].target_url, "https://target.example/article/")
        self.assertEqual(SentWebmention.objects.count(), 1)

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.services.send_webmention")
    def test_send_webmentions_for_post_skips_previously_sent_targets(self, send):
        post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="[Elsewhere](https://target.example/article/)",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )
        SentWebmention.objects.create(
            source_url="https://example.com/live-post/",
            target_url="https://target.example/article/",
            status=SentWebmention.SENT,
        )

        results = send_webmentions_for_post(post)

        self.assertEqual(results, [])
        send.assert_not_called()

    @override_settings(SITE_URL="https://example.com")
    @patch("webmentions.services.threading.Thread")
    def test_send_webmentions_for_post_async_starts_thread_on_commit(self, thread):
        post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="[Elsewhere](https://target.example/article/)",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            send_webmentions_for_post_async(post)

        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["args"], (post.pk,))
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once()

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.Session")
    def test_discovers_endpoint_from_link_header(self, session_class, resolve_ips):
        session_class.return_value = FakeSession(
            [
                FakeResponse(
                    200,
                    "<p>Hello</p>",
                    url=self.target,
                    headers={
                        "Content-Type": "text/html",
                        "Link": (
                            '<https://target.example/other/>; rel="author", '
                            '</webmention/?token=abc,def>; rel="WebMention"'
                        ),
                    },
                )
            ]
        )

        endpoint = discover_webmention_endpoint(self.target)

        self.assertEqual(endpoint, "https://target.example/webmention/?token=abc,def")

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.Session")
    def test_discovers_endpoint_from_html_link(self, session_class, resolve_ips):
        session_class.return_value = FakeSession(
            [
                FakeResponse(
                    200,
                    '<link rel="webmention" href="/webmention/">',
                    url="https://target.example/article/",
                    headers={"Content-Type": "text/html"},
                )
            ]
        )

        endpoint = discover_webmention_endpoint("https://target.example/article/")

        self.assertEqual(endpoint, "https://target.example/webmention/")

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.post")
    @patch("webmentions.services.requests.Session")
    def test_send_webmention_posts_source_and_target(
        self,
        session_class,
        post,
        resolve_ips,
    ):
        session_class.return_value = FakeSession(
            [
                FakeResponse(
                    200,
                    "<p>Hello</p>",
                    url=self.target,
                    headers={"Link": f"<{self.endpoint}>; rel=\"webmention\""},
                )
            ]
        )
        post.return_value = FakeResponse(202, "", url=self.endpoint)

        result = send_webmention(self.source, self.target)

        self.assertEqual(result.status, SentWebmention.SENT)
        post.assert_called_once_with(
            self.endpoint,
            data={"source": self.source, "target": self.target},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "antoniosantos.io webmention sender",
            },
            timeout=10,
            allow_redirects=False,
        )
        record = SentWebmention.objects.get(
            source_url=self.source,
            target_url=self.target,
        )
        self.assertEqual(record.endpoint_url, self.endpoint)
        self.assertEqual(record.status, SentWebmention.SENT)
        self.assertEqual(record.response_code, 202)
        self.assertEqual(record.attempts, 1)

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.post")
    @patch("webmentions.services.requests.Session")
    def test_send_webmention_records_missing_endpoint(
        self,
        session_class,
        post,
        resolve_ips,
    ):
        session_class.return_value = FakeSession(
            [
                FakeResponse(
                    200,
                    "<p>No endpoint</p>",
                    url=self.target,
                    headers={"Content-Type": "text/html"},
                )
            ]
        )

        result = send_webmention(self.source, self.target)

        self.assertEqual(result.status, SentWebmention.NO_ENDPOINT)
        post.assert_not_called()
        record = SentWebmention.objects.get(
            source_url=self.source,
            target_url=self.target,
        )
        self.assertEqual(record.status, SentWebmention.NO_ENDPOINT)
        self.assertEqual(record.attempts, 1)

    @patch("webmentions.services._resolve_host_ips", return_value=public_ip)
    @patch("webmentions.services.requests.post")
    @patch("webmentions.services.requests.Session")
    def test_send_webmention_records_non_success_response(
        self,
        session_class,
        post,
        resolve_ips,
    ):
        session_class.return_value = FakeSession(
            [
                FakeResponse(
                    200,
                    "<p>Hello</p>",
                    url=self.target,
                    headers={"Link": f"<{self.endpoint}>; rel=\"webmention\""},
                )
            ]
        )
        post.return_value = FakeResponse(500, "Nope", url=self.endpoint)

        result = send_webmention(self.source, self.target)

        self.assertEqual(result.status, SentWebmention.FAILED)
        record = SentWebmention.objects.get(
            source_url=self.source,
            target_url=self.target,
        )
        self.assertEqual(record.status, SentWebmention.FAILED)
        self.assertEqual(record.response_code, 500)
        self.assertEqual(record.error, "Nope")


class FakeResponse:
    encoding = "utf-8"

    def __init__(self, status_code, body, url, headers=None):
        self.status_code = status_code
        self.body = body
        self.url = url
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size=1, decode_unicode=False):
        if decode_unicode:
            yield self.body
        else:
            yield self.body.encode(self.encoding)

    @property
    def text(self):
        return self.body

    def close(self):
        self.closed = True


class FakeSession:
    max_redirects = None

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)
