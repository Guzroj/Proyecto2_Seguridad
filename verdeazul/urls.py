"""
Rutas del proyecto.

allauth se monta en /cuentas/. Se deshabilitan (404) el restablecimiento de
contraseña por correo y la gestión de correos: las cuentas las administra AC
(C15) y no hay canal de correo configurado. La primera contraseña se fija con
el enlace de activación que emite AC (cuentas/activar/...).
"""
from django.contrib import admin
from django.urls import include, path, re_path

from cuentas import views as cuentas_views

urlpatterns = [
    path("", cuentas_views.inicio, name="inicio"),
    re_path(r"^cuentas/password/reset/", cuentas_views.no_disponible),
    re_path(r"^cuentas/email/", cuentas_views.no_disponible),
    path("cuentas/activar/<uidb64>/<token>/", cuentas_views.ActivarCuentaView.as_view(), name="cuenta_activar"),
    path("cuentas/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]
