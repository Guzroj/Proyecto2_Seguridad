from django.urls import path

from . import pantallas

urlpatterns = [
    path("periodos/", pantallas.periodos, name="mediciones_periodos"),
    path("periodos/abrir/", pantallas.periodo_abrir, name="mediciones_periodo_abrir"),
    path("periodos/<int:pk>/verificacion/", pantallas.periodo_pasar, name="mediciones_periodo_pasar"),
    path("periodos/<int:pk>/cerrar/", pantallas.periodo_cerrar, name="mediciones_periodo_cerrar"),
    path("asignaciones/", pantallas.asignaciones, name="mediciones_asignaciones"),
    path("asignaciones/nueva/", pantallas.asignacion_nueva, name="mediciones_asignacion_nueva"),
    path("asignaciones/<int:pk>/terminar/", pantallas.asignacion_terminar, name="mediciones_asignacion_terminar"),
    path("mediciones/", pantallas.mediciones, name="mediciones_mediciones"),
    path("mediciones/nueva/", pantallas.medicion_nueva, name="mediciones_medicion_nueva"),
    path("mediciones/<uuid:pk>/", pantallas.medicion, name="mediciones_medicion"),
    path("mediciones/<uuid:pk>/modificar/", pantallas.medicion_modificar, name="mediciones_medicion_modificar"),
    path("mediciones/<uuid:pk>/enviar/", pantallas.medicion_enviar, name="mediciones_medicion_enviar"),
    path("mediciones/<uuid:pk>/aprobar/", pantallas.medicion_aprobar, name="mediciones_medicion_aprobar"),
    path("mediciones/<uuid:pk>/devolver/", pantallas.medicion_devolver, name="mediciones_medicion_devolver"),
    path("mediciones/<uuid:pk>/enmienda/", pantallas.enmienda_solicitar, name="mediciones_enmienda_solicitar"),
    path("enmiendas/", pantallas.enmiendas, name="mediciones_enmiendas"),
    path("enmiendas/<int:pk>/autorizar/", pantallas.enmienda_autorizar, name="mediciones_enmienda_autorizar"),
    path("enmiendas/<int:pk>/rechazar/", pantallas.enmienda_rechazar, name="mediciones_enmienda_rechazar"),
]
