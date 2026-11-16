from django.urls import path

from . import pantallas

urlpatterns = [
    path("cuentas/", pantallas.cuentas, name="gestion_cuentas"),
    path("cuentas/nueva/", pantallas.cuenta_nueva, name="gestion_cuenta_nueva"),
    path("cuentas/<int:pk>/", pantallas.cuenta, name="gestion_cuenta"),
    path("cuentas/<int:pk>/modificar/", pantallas.cuenta_modificar, name="gestion_cuenta_modificar"),
    path("cuentas/<int:pk>/baja/", pantallas.cuenta_baja, name="gestion_cuenta_baja"),
    path("cuentas/<int:pk>/enlace/", pantallas.cuenta_enlace, name="gestion_cuenta_enlace"),
    path("mi-cuenta/", pantallas.mi_cuenta, name="mi_cuenta"),
]
