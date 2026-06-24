from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone

from .forms import SubscribeForm
from .models import Post, PostMedia, Subscriber


def published_posts():
    return Post.objects.filter(
        status=Post.PUBLISHED,
        published_at__lte=timezone.now(),
    )


def home(request):
    posts = published_posts()[:5]
    return render(request, "blog/home.html", {"posts": posts})


def archive(request):
    posts = published_posts()
    return render(request, "blog/archive.html", {"posts": posts})


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
    return render(
        request,
        "blog/post_detail.html",
        {
            "post": post,
            "canonical_url": request.build_absolute_uri(post.get_absolute_url()),
        },
    )


def legacy_post_redirect(request, slug):
    return redirect("blog:post_detail", slug=slug, permanent=True)


def media_detail(request, slug):
    media = get_object_or_404(PostMedia, slug=slug)
    return redirect(media.file.url)
