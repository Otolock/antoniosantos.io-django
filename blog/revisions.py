from difflib import unified_diff

from django.utils.dateparse import parse_datetime

from .models import ContentRevision, Note, Post


def content_type_for(obj):
    if isinstance(obj, Post):
        return ContentRevision.POST
    if isinstance(obj, Note):
        return ContentRevision.NOTE
    raise TypeError(f"Unsupported revision model: {type(obj)!r}")


def snapshot_content(obj):
    snapshot = {
        "slug": obj.slug,
        "body": obj.body,
        "status": obj.status,
        "published_at": obj.published_at.isoformat() if obj.published_at else None,
        "tags": list(obj.tags.values_list("pk", flat=True)),
    }
    if isinstance(obj, Post):
        snapshot.update(
            {
                "title": obj.title,
                "description": obj.description,
                "reply_to_url": obj.reply_to_url,
                "reply_to_title": obj.reply_to_title,
                "upvotes_count": obj.upvotes_count,
            }
        )
    return snapshot


def create_revision(obj, user=None, reason="Saved"):
    if not obj.pk:
        return None
    return ContentRevision.objects.create(
        content_type=content_type_for(obj),
        object_id=obj.pk,
        object_label=str(obj),
        snapshot=snapshot_content(obj),
        reason=reason,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )


def restore_revision(obj, revision):
    if revision.content_type != content_type_for(obj) or revision.object_id != obj.pk:
        raise ValueError("Revision does not belong to this object.")

    snapshot = revision.snapshot
    scalar_fields = ["slug", "body", "status"]
    if isinstance(obj, Post):
        scalar_fields.extend(
            [
                "title",
                "description",
                "reply_to_url",
                "reply_to_title",
                "upvotes_count",
            ]
        )

    for field in scalar_fields:
        if field in snapshot:
            setattr(obj, field, snapshot[field])

    published_at = snapshot.get("published_at")
    obj.published_at = parse_datetime(published_at) if published_at else None
    obj.save()
    obj.tags.set(snapshot.get("tags", []))
    return obj


def revision_diff(older, newer):
    if older is None:
        return "Initial saved version."
    older_body = older.snapshot.get("body", "").splitlines()
    newer_body = newer.snapshot.get("body", "").splitlines()
    diff = list(
        unified_diff(
            older_body,
            newer_body,
            fromfile="Previous version",
            tofile="This version",
            lineterm="",
        )
    )
    return "\n".join(diff) or "No body changes in this version."
