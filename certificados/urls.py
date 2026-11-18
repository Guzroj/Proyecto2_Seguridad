from django.urls import path

from . import pantallas

# Bajo /gestion/ (con sesión): lectura según la matriz.
urlpatterns = [
    path("certificados/", pantallas.certificados, name="certificados_certificados"),
    path("certificados/<uuid:pk>/", pantallas.certificado, name="certificados_certificado"),
    path("certificados/<uuid:pk>/descargar/", pantallas.certificado_descargar, name="certificados_descargar"),
]
