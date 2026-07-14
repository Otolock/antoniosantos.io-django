from django.contrib import messages
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from .forms import SubscribeForm
from .models import Note, Post, PostMedia, Subscriber, Tag
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
    posts = published_entries()[:5]
    return render(request, "blog/home.html", {"posts": posts})


def archive(request):
    posts = published_entries()
    return render(request, "blog/archive.html", {"posts": posts})


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
    if request.method != "POST":
        return render(request, "blog/subscribe.html")

    redirect_to = request.POST.get("next") or reverse("blog:subscribe")
    if not url_has_allowed_host_and_scheme(
        redirect_to,
        allowed_hosts={request.get_host()},
    ):
        redirect_to = reverse("blog:home")

    form = SubscribeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid email address.")
        return redirect(redirect_to)

    email = form.cleaned_data["email"].strip().lower()
    _, created = Subscriber.objects.get_or_create(
        email=email,
        defaults={"source_path": redirect_to[:300]},
    )

    if created:
        messages.success(request, "Thanks. I'll keep you posted.")
    else:
        messages.info(request, "You're already on the interest list.")

    return redirect(redirect_to)


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
