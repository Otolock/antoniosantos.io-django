import uuid

from django.db import models


def _upload_path(instance, filename):
    """Store media uploads under an unguessable UUID path (spec §3.6.4)."""
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1]
    return f"micropub/{uuid.uuid4().hex}{ext}"


class MediaUpload(models.Model):
    file = models.FileField(upload_to=_upload_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.file.name
