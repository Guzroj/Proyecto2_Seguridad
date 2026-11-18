from django.urls import path

from . import pantallas

urlpatterns = [
    path("exportaciones/", pantallas.exportaciones, name="exportaciones_lista"),
    path("exportaciones/<slug:nombre>/", pantallas.exportar, name="exportaciones_exportar"),
]
