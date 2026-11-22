from django.conf import settings
from django.core.files.storage import FileSystemStorage

# base_url=None makes `.url` raise instead of returning a public link — so a
# template that forgets and reaches for `field.url` on a private upload fails
# loudly in dev rather than quietly serving something meant to stay private.
# The only supported way to reach one of these files is the matching
# permission-checked view (nid_image_view, education_document_view).
private_storage = FileSystemStorage(
    location=settings.PRIVATE_MEDIA_ROOT,
    base_url=None,
)
