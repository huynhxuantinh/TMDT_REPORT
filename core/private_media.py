"""Medical images live outside public MEDIA_ROOT, behind per-request access checks."""
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.urls import reverse


@deconstructible
class PrivateExamStorage(FileSystemStorage):
    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        root = Path(self.base_location).resolve()
        public = Path(settings.MEDIA_ROOT).resolve()
        if root == public or public in root.parents or root in public.parents:
            raise ImproperlyConfigured('PRIVATE_MEDIA_ROOT phải tách biệt MEDIA_ROOT.')
        return str(root)

    def url(self, name):
        return reverse('private_exam_image', kwargs={'path': name})


private_exam_storage = PrivateExamStorage()
