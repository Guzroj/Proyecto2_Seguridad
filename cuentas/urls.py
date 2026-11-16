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
    path("cuentas/<int:pk>/roles/asignar/", pantallas.rol_asignar, name="gestion_rol_asignar"),
    path("cuentas/<int:pk>/roles/<str:rol>/retirar/", pantallas.rol_retirar, name="gestion_rol_retirar"),
    path("solicitudes-rol/", pantallas.solicitudes_rol, name="gestion_solicitudes_rol"),
    path("solicitudes-rol/<int:pk>/autorizar/", pantallas.solicitud_autorizar, name="gestion_solicitud_autorizar"),
    path("solicitudes-rol/<int:pk>/rechazar/", pantallas.solicitud_rechazar, name="gestion_solicitud_rechazar"),
    path("ventanas/", pantallas.ventanas, name="gestion_ventanas"),
    path("ventanas/<int:pk>/aprobar/", pantallas.ventana_aprobar, name="gestion_ventana_aprobar"),
]
