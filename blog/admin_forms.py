from django import forms
from django.urls import reverse

from .models import Post, PostMedia


class PostBodyWidget(forms.Textarea):
    template_name = "admin/blog/post/body_widget.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["media_items"] = PostMedia.objects.all()
        context["media_upload_url"] = reverse("admin:blog_postmedia_composer_upload")
        return context


class PostAdminForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = "__all__"
        widgets = {
            "body": PostBodyWidget(
                attrs={
                    "rows": 28,
                    "class": "post-composer__body vLargeTextField",
                    "placeholder": "Tell the story behind the photograph…",
                    "spellcheck": "true",
                }
            ),
        }

    class Media:
        css = {"all": ("blog/admin/post_composer.css",)}
        js = ("blog/admin/post_composer.js",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["body"].help_text = (
            "Write in Markdown. Select photos from the library below to insert them "
            "at the cursor."
        )
