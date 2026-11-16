"""
Disparadores de catalogo.0003 y mediciones.0003: la base revalida las reglas aunque
alguien se salte los servicios. Estas pruebas atacan con SQL directo como el dueño
de la base (superusuario en local, que se salta RLS): ni así se rompen H4, C2, C13,
C19, C20 ni RG1. Los disparadores aplican a cualquiera; RLS es otra capa aparte.
"""

from django.db import DatabaseError, connection, transaction

from autorizacion.test_integracion_o1_o3 import EscenarioBase
from cuentas import rls
from mediciones import services
from mediciones.models import Asignacion, EstadoMedicion, Medicion, MedicionActor, Periodo


class DisparadoresBase(EscenarioBase):
    def falla(self, patron: str, consulta: str, params=()):
        with self.assertRaisesRegex(DatabaseError, patron), transaction.atomic(), connection.cursor() as c:
            c.execute(consulta, params)

    def ejecuta(self, consulta: str, params=()) -> int:
        with transaction.atomic(), connection.cursor() as c:
            c.execute(consulta, params)
            return c.rowcount

    def aprobar(self):
        self.enviar()
        self.medicion = services.aprobar_medicion(self.vf, self.medicion)


class ActoresTests(DisparadoresBase):
    def test_historial_de_actores_es_solo_insercion(self):
        actor = MedicionActor.objects.filter(medicion=self.medicion).first()
        self.falla("solo inserción", "UPDATE mediciones_medicionactor SET usuario_id = %s WHERE id = %s",
                   [self.vf.pk, actor.pk])
        self.falla("solo inserción", "DELETE FROM mediciones_medicionactor WHERE id = %s", [actor.pk])


class MedicionTests(DisparadoresBase):
    def test_h4_una_aprobada_no_cambia_ni_por_sql(self):
        self.aprobar()
        m = str(self.medicion.pk)
        self.falla("H4", "UPDATE mediciones_medicion SET numero_arboles = 999 WHERE id = %s", [m])
        self.falla("H4", "UPDATE mediciones_medicion SET estado = 'BORRADOR' WHERE id = %s", [m])
        self.assertEqual(Medicion.objects.get(pk=m).numero_arboles, 120)

    def test_transicion_invalida_y_datos_fuera_de_borrador(self):
        m = str(self.medicion.pk)
        self.falla("Transición", "UPDATE mediciones_medicion SET estado = 'APROBADA', verificador_id = %s, "
                   "aprobada_en = now() WHERE id = %s", [self.vf.pk, m])  # BORRADOR -> APROBADA
        self.enviar()
        self.falla("datos de campo", "UPDATE mediciones_medicion SET numero_arboles = 1 WHERE id = %s", [m])  # ENVIADA
        self.falla("verificador", "UPDATE mediciones_medicion SET verificador_id = %s WHERE id = %s", [self.vf2.pk, m])

    def test_identidad_no_cambia(self):
        self.falla("autor", "UPDATE mediciones_medicion SET autor_id = %s WHERE id = %s",
                   [self.tc2.pk, str(self.medicion.pk)])

    def test_rg1_no_se_borra(self):
        self.falla("RG1", "DELETE FROM mediciones_medicion WHERE id = %s", [str(self.medicion.pk)])

    def test_nace_en_borrador_y_periodo_abierto(self):
        self.falla("C1", "INSERT INTO mediciones_medicion (id, parcela_id, parcela_version_id, periodo_id, autor_id, "
                   "estado, numero_arboles, dap_promedio_cm, altura_promedio_m, observaciones, toneladas_co2, "
                   "creada_en, actualizada_en, comentario_devolucion, verificador_id) "
                   "SELECT gen_random_uuid(), parcela_id, parcela_version_id, periodo_id, autor_id, 'APROBADA', "
                   "1, 1, 1, '', 1, now(), now(), '', %s FROM mediciones_medicion WHERE id = %s",
                   [self.vf.pk, str(self.medicion.pk)])

    def test_c2_con_sujeto_solo_aprueba_el_verificador_asignado(self):
        self.enviar()
        m = str(self.medicion.pk)
        aprobar = ("UPDATE mediciones_medicion SET estado = 'APROBADA', aprobada_en = now() WHERE id = %s")
        rls.fijar_contexto(usuario_id=self.vf2.pk)  # otro VF conectado
        try:
            self.falla("C2", aprobar, [m])
        finally:
            rls.fijar_contexto()
        rls.fijar_contexto(usuario_id=self.vf.pk)   # el asignado sí
        try:
            self.assertEqual(self.ejecuta(aprobar, [m]), 1)
        finally:
            rls.fijar_contexto()
        self.assertEqual(Medicion.objects.get(pk=m).estado, EstadoMedicion.APROBADA)


class PeriodoTests(DisparadoresBase):
    def test_c13_solo_avanza_y_no_se_cierra_con_pendientes(self):
        p = self.periodo.pk
        self.falla("C13", "UPDATE mediciones_periodo SET estado = 'CERRADO', cerrado_por_id = %s, "
                   "cerrado_en = now() WHERE id = %s", [self.gc.pk, p])  # ABIERTO -> CERRADO
        self.enviar()
        services.pasar_a_verificacion(self.gc, self.periodo)
        self.falla("C13", "UPDATE mediciones_periodo SET estado = 'ABIERTO' WHERE id = %s", [p])  # reabrir
        self.falla("pendientes|ENVIADA", "UPDATE mediciones_periodo SET estado = 'CERRADO', cerrado_por_id = %s, "
                   "cerrado_en = now() WHERE id = %s", [self.gc.pk, p])  # V16

    def test_cerrado_no_cambia(self):
        services.pasar_a_verificacion(self.gc, self.periodo)
        services.cerrar_periodo(self.gc, self.periodo)  # la medición en BORRADOR no bloquea el cierre (C13)
        self.falla("CERRADO", "UPDATE mediciones_periodo SET nombre = 'otro' WHERE id = %s", [self.periodo.pk])
        self.assertEqual(Periodo.objects.get(pk=self.periodo.pk).nombre, "2026-A")


class AsignacionTests(DisparadoresBase):
    def test_c19_propietario_y_solo_se_termina_una_vez(self):
        self.falla("C19", "INSERT INTO mediciones_asignacion (tecnico_id, finca_id, periodo_id, vigente_desde, "
                   "creada_por_id) VALUES (%s, %s, %s, now(), %s)",
                   [self.pf.pk, self.finca.pk, self.periodo.pk, self.ac.pk])
        services.terminar_asignacion(self.ac, self.asignacion)
        self.falla("C19", "UPDATE mediciones_asignacion SET vigente_hasta = now() WHERE id = %s", [self.asignacion.pk])
        self.falla("RG1", "DELETE FROM mediciones_asignacion WHERE id = %s", [self.asignacion.pk])
        self.assertTrue(Asignacion.objects.filter(pk=self.asignacion.pk).exists())


class CatalogoTests(DisparadoresBase):
    def test_c20_version_de_geometria_inmutable(self):
        v = self.parcela.version_vigente()
        self.falla("C20", "UPDATE catalogo_parcelaversion SET area_ha = 99 WHERE id = %s", [v.pk])
        self.assertEqual(self.ejecuta("UPDATE catalogo_parcelaversion SET vigente_hasta = clock_timestamp() WHERE id = %s",
                                      [v.pk]), 1)  # cerrar la vigencia sí, una vez
        self.falla("C20", "UPDATE catalogo_parcelaversion SET vigente_hasta = clock_timestamp() WHERE id = %s", [v.pk])

    def test_finca_y_parcela(self):
        self.falla("propietario", "UPDATE catalogo_finca SET propietario_id = %s WHERE id = %s",
                   [self.pf2.pk, self.finca.pk])
        self.falla("código", "UPDATE catalogo_parcela SET codigo = 'X' WHERE id = %s", [self.parcela.pk])
        for tabla, pk in (("catalogo_finca", self.finca.pk), ("catalogo_parcela", self.parcela.pk)):
            with self.subTest(tabla=tabla):
                self.falla("RG1", f"DELETE FROM {tabla} WHERE id = %s", [pk])
