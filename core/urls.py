from django.contrib import admin
from django.urls import path

from . import admin_site  # noqa: F401
from .health import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
]
