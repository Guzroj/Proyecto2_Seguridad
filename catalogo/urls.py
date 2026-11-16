from django.urls import path

from . import pantallas

urlpatterns = [
    path("fincas/", pantallas.fincas, name="catalogo_fincas"),
    path("fincas/nueva/", pantallas.finca_nueva, name="catalogo_finca_nueva"),
    path("fincas/<int:pk>/", pantallas.finca, name="catalogo_finca"),
    path("fincas/<int:pk>/modificar/", pantallas.finca_modificar, name="catalogo_finca_modificar"),
    path("fincas/<int:pk>/baja/", pantallas.finca_baja, name="catalogo_finca_baja"),
    path("fincas/<int:pk>/propietario/", pantallas.propietario_datos, name="catalogo_propietario_datos"),
    path("fincas/<int:pk>/parcelas/nueva/", pantallas.parcela_nueva, name="catalogo_parcela_nueva"),
    path("parcelas/<int:pk>/", pantallas.parcela, name="catalogo_parcela"),
    path("parcelas/<int:pk>/geometria/", pantallas.parcela_geometria, name="catalogo_parcela_geometria"),
    path("parcelas/<int:pk>/baja/", pantallas.parcela_baja, name="catalogo_parcela_baja"),
]
