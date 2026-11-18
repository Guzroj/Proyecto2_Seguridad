from django.urls import path

from . import pantallas

# Bajo /certificados/ (públicas): verificar un documento y conocer las claves de Verdeazul.
urlpatterns = [
    path("verificar/", pantallas.verificar, name="certificados_verificar"),
    path("clave-publica/", pantallas.clave_publica, name="certificados_clave_publica"),
]
