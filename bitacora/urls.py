from django.urls import path

from . import pantallas

urlpatterns = [
    path("bitacora/", pantallas.eventos, name="bitacora_eventos"),
    path("bitacora/verificar/", pantallas.verificar, name="bitacora_verificar"),
    path("bitacora/<uuid:pk>/", pantallas.evento, name="bitacora_evento"),
]
