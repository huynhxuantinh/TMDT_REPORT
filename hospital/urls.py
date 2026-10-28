"""
URL configuration for hospital project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from core import views as core_views

urlpatterns = [
    path("", include("core.urls")),
]

handler404 = "core.views.custom_404"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += [
        re_path(r"^.*$", core_views.custom_404_debug),
    ]
