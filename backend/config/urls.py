from django.contrib import admin
from django.urls import path

from config.api import api

admin.site.site_header = "Trade desk admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
