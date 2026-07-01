import json

from django.test import TestCase, override_settings
from django.urls import reverse

from blog.models import Post


@override_settings(MICROPUB_TOKEN="test-token")
class MicropubTests(TestCase):
    def auth(self):
        return {"HTTP_AUTHORIZATION": "Bearer test-token"}

    def test_config_requires_bearer_token(self):
        response = self.client.get(
            reverse("micropub:endpoint"),
            {"q": "config"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("WWW-Authenticate", response)

    def test_config_returns_supported_post_types(self):
        response = self.client.get(
            reverse("micropub:endpoint"),
            {"q": "config"},
            **self.auth(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["post-types"],
            [{"type": "article", "name": "Draft post"}],
        )

    def test_config_accepts_access_token_query_parameter(self):
        response = self.client.get(
            reverse("micropub:endpoint"),
            {"q": "config", "access_token": "test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["post-types"],
            [{"type": "article", "name": "Draft post"}],
        )

    def test_form_create_uses_leading_heading_as_title_not_client_name(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "name": "ia-writer-filename",
                "post-status": "draft",
                "content": "# Real Post Title\n\nThis is the draft body.",
            },
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "Real Post Title")
        self.assertEqual(post.slug, "real-post-title")
        self.assertEqual(post.body, "This is the draft body.")
        self.assertEqual(post.status, Post.DRAFT)
        self.assertIsNone(post.published_at)
        self.assertEqual(response["Location"], "http://testserver/real-post-title/")

    def test_form_create_uses_name_when_content_has_no_heading(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "name": "Form Post Title",
                "content": "This is the draft body.",
            },
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "Form Post Title")
        self.assertEqual(post.slug, "form-post-title")
        self.assertEqual(post.body, "This is the draft body.")
        self.assertEqual(post.status, Post.DRAFT)

    def test_form_create_accepts_minimal_content_only_entry(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "content": "Hello World",
            },
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "Hello World")
        self.assertEqual(post.slug, "hello-world")
        self.assertEqual(post.body, "Hello World")

    def test_form_create_defaults_to_entry_when_h_is_omitted(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"content": "Hello World"},
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "Hello World")

    def test_form_create_accepts_access_token_body_parameter(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "content": "Hello World",
                "access_token": "test-token",
            },
        )

        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "Hello World")

    def test_create_accepts_exact_micropub_url_without_trailing_slash(self):
        response = self.client.post(
            "/micropub",
            {
                "h": "entry",
                "content": "# Real Post Title\n\nThis is the draft body.",
            },
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "Real Post Title")

    def test_json_create_uses_microformats_content_property(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "type": ["h-entry"],
                    "properties": {
                        "name": ["draft-file-name"],
                        "content": [
                            {
                                "value": "Markdown Title\n==============\n\nBody text.",
                            }
                        ],
                    },
                }
            ),
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "Markdown Title")
        self.assertEqual(post.body, "Body text.")

    def test_json_create_uses_name_property_when_content_has_no_heading(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "type": ["h-entry"],
                    "properties": {
                        "name": ["JSON Post Title"],
                        "content": ["Body text."],
                    },
                }
            ),
            content_type="application/json",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "JSON Post Title")
        self.assertEqual(post.body, "Body text.")

    def test_create_requires_content(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "name": "filename-title",
                "content": "",
            },
            **self.auth(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Post.objects.count(), 0)
        self.assertEqual(response.json()["error"], "invalid_request")

    def test_create_generates_unique_slug_from_heading(self):
        Post.objects.create(
            title="Real Post Title",
            slug="real-post-title",
            body="Existing body",
            status=Post.DRAFT,
        )

        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "content": "# Real Post Title\n\nSecond draft.",
            },
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201)
        post = Post.objects.get(body="Second draft.")
        self.assertEqual(post.slug, "real-post-title-2")

    def test_create_rejects_invalid_token(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "content": "# Real Post Title\n\nThis is the draft body.",
            },
            HTTP_AUTHORIZATION="Bearer wrong-token",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Post.objects.count(), 0)
