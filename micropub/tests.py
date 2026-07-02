import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from blog.models import Post
from indieauth.models import AccessToken
from micropub.models import MediaUpload


@override_settings(SITE_URL="https://antoniosantos.io")
class MicropubTests(TestCase):
    def setUp(self):
        self.token_value = "test-access-token"
        self.access_token = AccessToken.objects.create(
            token=self.token_value,
            client_id="https://ia-writer.example/",
            scope="create update delete",
            me="https://antoniosantos.io/",
        )

    def auth(self):
        return {"HTTP_AUTHORIZATION": "Bearer test-access-token"}

    def _make_token(self, scope, token_value=None):
        return AccessToken.objects.create(
            token=token_value or f"token-{scope}-{AccessToken.objects.count()}",
            client_id="https://client.example/",
            scope=scope,
            me="https://antoniosantos.io/",
        )

    # --- Configuration query ------------------------------------------------

    def test_config_requires_bearer_token(self):
        response = self.client.get(reverse("micropub:endpoint"), {"q": "config"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("WWW-Authenticate", response)
        self.assertEqual(response.json()["error"], "unauthorized")

    def test_config_returns_media_endpoint_and_post_types(self):
        response = self.client.get(
            reverse("micropub:endpoint"), {"q": "config"}, **self.auth()
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            data["post-types"], [{"type": "article", "name": "Draft post"}]
        )
        self.assertIn("media-endpoint", data)
        self.assertTrue(data["media-endpoint"].endswith("/micropub/media/"))
        self.assertEqual(data["syndicate-to"], [])

    def test_config_accepts_access_token_query_parameter(self):
        response = self.client.get(
            reverse("micropub:endpoint"),
            {"q": "config", "access_token": "test-access-token"},
        )
        self.assertEqual(response.status_code, 200)

    def test_syndicate_to_returns_empty_list(self):
        response = self.client.get(
            reverse("micropub:endpoint"), {"q": "syndicate-to"}, **self.auth()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"syndicate-to": []})

    def test_unsupported_query_returns_error(self):
        response = self.client.get(
            reverse("micropub:endpoint"), {"q": "unknown"}, **self.auth()
        )
        self.assertEqual(response.status_code, 400)

    # --- Create: form-encoded -----------------------------------------------

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
            {"h": "entry", "name": "Form Post Title", "content": "This is the draft body."},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "Form Post Title")
        self.assertEqual(post.body, "This is the draft body.")

    def test_form_create_accepts_minimal_content_only_entry(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"h": "entry", "content": "Hello World"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "Hello World")
        self.assertEqual(post.body, "Hello World")

    def test_form_create_defaults_to_entry_when_h_is_omitted(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"content": "Hello World"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)

    def test_form_create_accepts_access_token_body_parameter(self):
        token = self._make_token("create", "body-token-create")
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"h": "entry", "content": "Hello World", "access_token": token.token},
        )
        self.assertEqual(response.status_code, 201)

    def test_form_create_accepts_category_array_syntax(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "content": "Hello World",
                "category[]": ["foo", "bar"],
            },
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        # Categories are ignored (not stored), but the post is still created.
        post = Post.objects.get()
        self.assertEqual(post.title, "Hello World")

    def test_form_create_ignores_unrecognized_properties(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "content": "Hello World",
                "in-reply-to": "https://example.com/post",
                "mp-syndicate-to": "https://archive.org/",
            },
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.body, "Hello World")

    def test_create_accepts_exact_micropub_url_without_trailing_slash(self):
        response = self.client.post(
            "/micropub",
            {"h": "entry", "content": "# Real Post Title\n\nThis is the draft body."},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Post.objects.get().title, "Real Post Title")

    # --- Create: JSON -------------------------------------------------------

    def test_json_create_uses_microformats_content_property(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "type": ["h-entry"],
                    "properties": {
                        "name": ["draft-file-name"],
                        "content": [{"value": "Markdown Title\n==============\n\nBody text."}],
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
                    "properties": {"name": ["JSON Post Title"], "content": ["Body text."]},
                }
            ),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.title, "JSON Post Title")
        self.assertEqual(post.body, "Body text.")

    def test_json_create_accepts_html_content(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "type": ["h-entry"],
                    "properties": {
                        "name": ["HTML Post"],
                        "content": [{"html": "<p>Hello <b>World</b></p>"}],
                    },
                }
            ),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertEqual(post.body, "<p>Hello <b>World</b></p>")

    def test_json_create_embeds_photo_url_in_body(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "type": ["h-entry"],
                    "properties": {
                        "name": ["Photo Post"],
                        "content": ["A nice photo."],
                        "photo": ["https://photos.example.com/123.jpg"],
                    },
                }
            ),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertIn("![\\](https://photos.example.com/123.jpg)".replace("\\", ""), post.body)
        self.assertIn("A nice photo.", post.body)

    def test_json_create_embeds_photo_with_alt_text(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "type": ["h-entry"],
                    "properties": {
                        "name": ["Photo Post"],
                        "content": ["Body text."],
                        "photo": [
                            {
                                "value": "https://photos.example.com/globe.gif",
                                "alt": "Spinning globe",
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
        self.assertIn("![Spinning globe](https://photos.example.com/globe.gif)", post.body)

    def test_json_create_photo_only_no_content(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "type": ["h-entry"],
                    "properties": {
                        "name": ["Photo Only"],
                        "photo": ["https://photos.example.com/123.jpg"],
                    },
                }
            ),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertIn("![](https://photos.example.com/123.jpg)", post.body)

    # --- Create: multipart --------------------------------------------------

    def test_multipart_create_with_file_upload(self):
        upload = SimpleUploadedFile("photo.png", b"fake-png-data", content_type="image/png")
        response = self.client.post(
            reverse("micropub:endpoint"),
            {
                "h": "entry",
                "name": "Uploaded Photo",
                "content": "Check this out",
                "photo": upload,
            },
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get()
        self.assertIn("![](", post.body)
        self.assertEqual(MediaUpload.objects.count(), 1)

    # --- Create: validation -------------------------------------------------

    def test_create_requires_content(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"h": "entry", "name": "filename-title", "content": ""},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Post.objects.count(), 0)
        self.assertEqual(response.json()["error"], "invalid_request")

    def test_form_create_rejects_non_entry_h_value(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"h": "card", "content": "Hello World"},
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
            {"h": "entry", "content": "# Real Post Title\n\nSecond draft."},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get(body="Second draft.")
        self.assertEqual(post.slug, "real-post-title-2")

    # --- Source query -------------------------------------------------------

    def test_source_query_returns_full_post(self):
        post = Post.objects.create(
            title="My Post", slug="my-post", body="Hello world.", status=Post.DRAFT
        )
        response = self.client.get(
            reverse("micropub:endpoint"),
            {"q": "source", "url": f"https://antoniosantos.io/my-post/"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["type"], ["h-entry"])
        self.assertEqual(data["properties"]["name"], ["My Post"])
        self.assertEqual(data["properties"]["content"], ["Hello world."])

    def test_source_query_filters_requested_properties(self):
        post = Post.objects.create(
            title="My Post", slug="my-post", body="Hello world.", status=Post.DRAFT
        )
        response = self.client.get(
            reverse("micropub:endpoint"),
            {
                "q": "source",
                "url": f"https://antoniosantos.io/my-post/",
                "properties[]": ["name"],
            },
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["properties"], {"name": ["My Post"]})
        self.assertNotIn("content", data["properties"])

    def test_source_query_requires_update_scope(self):
        token = self._make_token("create")
        Post.objects.create(
            title="My Post", slug="my-post", body="Hello world.", status=Post.DRAFT
        )
        response = self.client.get(
            reverse("micropub:endpoint"),
            {"q": "source", "url": "https://antoniosantos.io/my-post/"},
            HTTP_AUTHORIZATION=f"Bearer {token.token}",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "insufficient_scope")

    def test_source_query_404_for_unknown_post(self):
        response = self.client.get(
            reverse("micropub:endpoint"),
            {"q": "source", "url": "https://antoniosantos.io/nope/"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_request")

    def test_source_query_404_for_deleted_post(self):
        Post.objects.create(
            title="Deleted", slug="deleted", body="bye", status=Post.DELETED
        )
        response = self.client.get(
            reverse("micropub:endpoint"),
            {"q": "source", "url": "https://antoniosantos.io/deleted/"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    # --- Update -------------------------------------------------------------

    def test_update_replaces_content(self):
        post = Post.objects.create(
            title="Old Title", slug="old-title", body="Old body.", status=Post.DRAFT
        )
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "action": "update",
                    "url": f"https://antoniosantos.io/old-title/",
                    "replace": {"content": ["New body."]},
                }
            ),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.body, "New body.")
        self.assertEqual(post.title, "Old Title")

    def test_update_replaces_name(self):
        post = Post.objects.create(
            title="Old Title", slug="old-title", body="Body.", status=Post.DRAFT
        )
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "action": "update",
                    "url": f"https://antoniosantos.io/old-title/",
                    "replace": {"name": ["New Title"]},
                }
            ),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.title, "New Title")

    def test_update_requires_json(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"action": "update", "url": "https://antoniosantos.io/x/", "replace[content]": "nope"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_update_requires_url(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps({"action": "update", "replace": {"content": ["x"]}}),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_update_rejects_non_array_property_values(self):
        Post.objects.create(
            title="Old Title", slug="old-title", body="Old body.", status=Post.DRAFT
        )
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "action": "update",
                    "url": "https://antoniosantos.io/old-title/",
                    "replace": {"content": "New body."},
                }
            ),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_request")

    def test_update_unknown_post_returns_error(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "action": "update",
                    "url": "https://antoniosantos.io/nope/",
                    "replace": {"content": ["x"]},
                }
            ),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_update_requires_update_scope(self):
        token = self._make_token("create")
        post = Post.objects.create(
            title="T", slug="t", body="b", status=Post.DRAFT
        )
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {
                    "action": "update",
                    "url": "https://antoniosantos.io/t/",
                    "replace": {"content": ["x"]},
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token.token}",
        )
        self.assertEqual(response.status_code, 403)

    # --- Delete / Undelete --------------------------------------------------

    def test_delete_marks_post_as_deleted(self):
        post = Post.objects.create(
            title="To Delete", slug="to-delete", body="bye", status=Post.DRAFT
        )
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"action": "delete", "url": "https://antoniosantos.io/to-delete/"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.status, Post.DELETED)

    def test_undelete_restores_post(self):
        post = Post.objects.create(
            title="Deleted", slug="deleted", body="bye", status=Post.DELETED
        )
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"action": "undelete", "url": "https://antoniosantos.io/deleted/"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.status, Post.DRAFT)

    def test_delete_via_json(self):
        post = Post.objects.create(
            title="JDel", slug="jdel", body="bye", status=Post.DRAFT
        )
        response = self.client.post(
            reverse("micropub:endpoint"),
            data=json.dumps(
                {"action": "delete", "url": "https://antoniosantos.io/jdel/"}
            ),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.status, Post.DELETED)

    def test_delete_unknown_post_returns_error(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"action": "delete", "url": "https://antoniosantos.io/nope/"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_already_deleted_returns_error(self):
        Post.objects.create(
            title="Del", slug="del", body="bye", status=Post.DELETED
        )
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"action": "delete", "url": "https://antoniosantos.io/del/"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_requires_delete_scope(self):
        token = self._make_token("create")
        Post.objects.create(title="T", slug="t", body="b", status=Post.DRAFT)
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"action": "delete", "url": "https://antoniosantos.io/t/"},
            HTTP_AUTHORIZATION=f"Bearer {token.token}",
        )
        self.assertEqual(response.status_code, 403)

    # --- Media endpoint -----------------------------------------------------

    def test_media_endpoint_uploads_file_and_returns_location(self):
        upload = SimpleUploadedFile("sunset.jpg", b"fake-jpg", content_type="image/jpeg")
        response = self.client.post(
            reverse("micropub:media_endpoint"),
            {"file": upload},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn("Location", response)
        self.assertEqual(MediaUpload.objects.count(), 1)

    def test_media_endpoint_requires_file_part(self):
        response = self.client.post(
            reverse("micropub:media_endpoint"), {}, **self.auth()
        )
        self.assertEqual(response.status_code, 400)

    def test_media_endpoint_rejects_unsafe_file_type(self):
        upload = SimpleUploadedFile(
            "payload.html", b"<script>alert(1)</script>", content_type="text/html"
        )
        response = self.client.post(
            reverse("micropub:media_endpoint"), {"file": upload}, **self.auth()
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MediaUpload.objects.count(), 0)

    def test_media_endpoint_requires_auth(self):
        upload = SimpleUploadedFile("s.jpg", b"x", content_type="image/jpeg")
        response = self.client.post(
            reverse("micropub:media_endpoint"), {"file": upload}
        )
        self.assertEqual(response.status_code, 401)

    # --- Authentication / errors --------------------------------------------

    def test_rejects_invalid_token(self):
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"h": "entry", "content": "# Real Post Title\n\nThis is the draft body."},
            HTTP_AUTHORIZATION="Bearer wrong-token",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Post.objects.count(), 0)
        self.assertEqual(response.json()["error"], "invalid_token")

    def test_rejects_revoked_token(self):
        self.access_token.revoked = True
        self.access_token.save(update_fields=["revoked"])
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"h": "entry", "content": "Hello World"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 401)

    def test_rejects_token_without_create_scope(self):
        token = self._make_token("profile")
        response = self.client.post(
            reverse("micropub:endpoint"),
            {"h": "entry", "content": "Hello World"},
            HTTP_AUTHORIZATION=f"Bearer {token.token}",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "insufficient_scope")
