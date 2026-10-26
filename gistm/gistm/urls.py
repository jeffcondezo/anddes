"""
URL configuration for gistm project.
"""
from django.urls import include, path

urlpatterns = [
    path("", include("maestro.urls")),
]
