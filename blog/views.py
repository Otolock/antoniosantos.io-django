from django.db.models import F
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from .models import Note, Post, PostMedia, Tag
from webmentions.models import Webmention


def published_posts():
    return Post.objects.filter(
        status=Post.PUBLISHED,
        published_at__lte=timezone.now(),
    )


def published_notes():
    return Note.objects.filter(
        status=Note.PUBLISHED,
        published_at__lte=timezone.now(),
    )


def published_entries():
    return sorted(
        [*published_posts(), *published_notes()],
        key=lambda entry: entry.published_at,
        reverse=True,
    )


def home(request):
    posts = published_posts()[:5]
    notes = published_notes()[:3]
    return render(
        request,
        "blog/home.html",
        {"posts": posts, "notes": notes},
    )


def archive(request):
    posts = published_posts()
    return render(request, "blog/archive.html", {"posts": posts})


def notes(request):
    return render(
        request,
        "blog/notes.html",
        {"notes": published_notes()},
    )


def tag_detail(request, slug):
    tag = get_object_or_404(Tag, slug=slug)
    posts = sorted(
        [
            *published_posts().filter(tags=tag).distinct(),
            *published_notes().filter(tags=tag).distinct(),
        ],
        key=lambda entry: entry.published_at,
        reverse=True,
    )
    return render(request, "blog/tag_detail.html", {"tag": tag, "posts": posts})


def now(request):
    return render(request, "blog/now.html")


def subscribe(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return render(request, "blog/subscribe.html")


def legacy_subscribe_redirect(request):
    return redirect("blog:subscribe", permanent=True)


def post_detail(request, slug):
    post = get_object_or_404(
        Post,
        slug=slug,
        status=Post.PUBLISHED,
        published_at__lte=timezone.now(),
    )

    if request.method == "POST":
        if request.POST.get("action") == "upvote":
            Post.objects.filter(pk=post.pk).update(
                upvotes_count=F("upvotes_count") + 1,
            )
            post.refresh_from_db(fields=["upvotes_count"])
            if request.headers.get("x-requested-with") == "fetch":
                return JsonResponse({"upvotes_count": post.upvotes_count})
            return redirect(f"{post.get_absolute_url()}#feedback-actions")

    canonical_url = request.build_absolute_uri(post.get_absolute_url())
    webmentions = Webmention.objects.filter(
        target_url=canonical_url,
        status=Webmention.APPROVED,
    ).order_by("created_at")
    return render(
        request,
        "blog/post_detail.html",
        {
            "post": post,
            "canonical_url": canonical_url,
            "webmentions": webmentions,
            "post_tags": post.tags.all(),
        },
    )


def note_detail(request, slug):
    note = get_object_or_404(
        Note,
        slug=slug,
        status=Note.PUBLISHED,
        published_at__lte=timezone.now(),
    )
    return render(
        request,
        "blog/note_detail.html",
        {
            "note": note,
            "canonical_url": request.build_absolute_uri(note.get_absolute_url()),
            "note_tags": note.tags.all(),
        },
    )


def legacy_post_redirect(request, slug):
    return redirect("blog:post_detail", slug=slug, permanent=True)


def media_detail(request, slug):
    media = get_object_or_404(PostMedia, slug=slug)
    return redirect(media.file.url)
