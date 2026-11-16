"""
Paso 17 · RLS de las tablas del dominio, con un rol sin privilegios equivalente a
app_runtime (SET ROLE) y los servicios reales autorizando con el motor.

17a: catálogo (O1a, O1b), períodos (O2) y asignaciones (O3). Lectura por finca
relacionada; escritura solo de quien la matriz deja escribir; nadie borra.
"""

import unittest

try:
    import django  # noqa: F401
except ImportError:
    raise unittest.SkipTest("Django no está instalado en esta imagen")

from contextlib import contextmanager
from datetime import date
from unittest import mock

from django.db import DatabaseError, connection, transaction
from django.test import override_settings

from autorizacion import cliente, pep
from autorizacion.listas import listar
from autorizacion.test_integracion_o1_o3 import POLIGONO
from autorizacion.test_integracion_o4_o6 import DATOS
from autorizacion.test_integracion_o7_o9 import GANCHOS
from autorizacion.test_listas import ListasBase
from catalogo import services as catalogo
from catalogo.models import DatosPropietario, Finca, Parcela, ParcelaVersion
from cuentas import rls
from cuentas.rls import FASE_PREINGRESO
from cuentas.test_rls import ROL
from mediciones import services as mediciones
from mediciones.models import Asignacion, Periodo
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado


class RLSDominioBase(ListasBase):
    def setUp(self):
        super().setUp()
        parche = override_settings(VERDEAZUL_GANCHOS=GANCHOS)  # los servicios autorizan con el motor real
        parche.enable()
        self.addCleanup(parche.disable)
        with connection.cursor() as c:
            c.execute(f"CREATE ROLE {ROL} NOLOGIN NOSUPERUSER NOBYPASSRLS")
            c.execute(f"GRANT USAGE ON SCHEMA public TO {ROL}")
            c.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROL}")
            c.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROL}")
            c.execute(f"GRANT EXECUTE ON FUNCTION cuentas_media_lecturas_rol(text, timestamptz, timestamptz) TO {ROL}")

    @contextmanager
    def como(self, usuario=None, fase=None):
        with connection.cursor() as c:
            c.execute(f"SET ROLE {ROL}")
        rls.fijar_contexto(usuario_id=getattr(usuario, "pk", None), fase=fase)
        try:
            yield
        finally:
            with connection.cursor() as c:
                c.execute("RESET ROLE")
            rls.fijar_contexto()

    def sql(self, consulta, params=()):
        """En un savepoint: filas afectadas, o la excepción si RLS la rechaza (y solo RLS)."""
        try:
            with transaction.atomic(), connection.cursor() as c:
                c.execute(consulta, params)
                return c.rowcount
        except DatabaseError as exc:
            self.assertIn("row-level security", str(exc))
            return exc

    def visibles(self, modelo, usuario=None, fase=None):
        with self.como(usuario, fase):
            return set(modelo.objects.values_list("pk", flat=True))


class LecturaCatalogoTests(RLSDominioBase):
    def test_fincas_por_finca_relacionada(self):
        todas = {self.finca.pk, self.finca2.pk, self.finca_mixta.pk}
        casos = [(self.pf, {self.finca.pk}), (self.pf2, {self.finca2.pk}), (self.tc, {self.finca.pk}),
                 (self.tc2, {self.finca2.pk}), (self.vf, {self.finca.pk}),  # verificador de una medición
                 (self.vf2, set()), (self.mixto, {self.finca_mixta.pk}),
                 (self.gc, todas), (self.ac, todas), (self.ae, todas)]
        for usuario, esperadas in casos:
            with self.subTest(usuario=usuario.username):
                self.assertEqual(self.visibles(Finca, usuario), esperadas)
                parcelas = set(Parcela.objects.filter(finca_id__in=esperadas).values_list("pk", flat=True))
                self.assertEqual(self.visibles(Parcela, usuario), parcelas)
                versiones = set(ParcelaVersion.objects.filter(parcela_id__in=parcelas).values_list("pk", flat=True))
                self.assertEqual(self.visibles(ParcelaVersion, usuario), versiones)
                asignaciones = set(Asignacion.objects.filter(finca_id__in=esperadas).values_list("pk", flat=True))
                self.assertEqual(self.visibles(Asignacion, usuario), asignaciones)

    def test_anonimo_y_preingreso_no_ven_nada(self):
        for modelo in (Finca, Parcela, ParcelaVersion, DatosPropietario, Periodo, Asignacion):
            with self.subTest(modelo=modelo.__name__):
                self.assertEqual(self.visibles(modelo), set())
                self.assertEqual(self.visibles(modelo, self.gc, fase=FASE_PREINGRESO), set())

    def test_datos_personales_solo_el_propietario_ac_y_ae(self):
        propios = DatosPropietario.objects.get(usuario=self.pf).pk
        self.assertEqual(self.visibles(DatosPropietario, self.pf), {propios})
        for usuario in (self.gc, self.tc, self.vf):
            self.assertEqual(self.visibles(DatosPropietario, usuario), set())
        for usuario in (self.ac, self.ae):
            self.assertEqual(len(self.visibles(DatosPropietario, usuario)), 2)

    def test_periodos_para_los_roles_con_o2(self):
        for usuario in (self.tc, self.vf, self.gc, self.ac, self.ae):
            self.assertEqual(self.visibles(Periodo, usuario), {self.periodo.pk})
        self.assertEqual(self.visibles(Periodo, self.pf), set())


class EscrituraCatalogoTests(RLSDominioBase):
    def test_flujo_por_servicios_bajo_rls(self):
        with self.como(self.ac):
            finca = catalogo.crear_finca(self.ac, "Nueva", "Guatuso", self.pf2)
            parcela = catalogo.crear_parcela(self.ac, self.finca, codigo="P3", area_ha="1", geometria=POLIGONO)
            catalogo.modificar_geometria(self.ac, parcela, area_ha="1.1", geometria=POLIGONO)
            catalogo.modificar_finca(self.ac, finca, nombre="Nueva Esperanza")
            catalogo.registrar_datos_propietario(self.ac, self.pf, nombre_completo="Otro nombre", identificacion="9-9")
            asignacion = mediciones.crear_asignacion(self.ac, self.tc2, self.finca, self.periodo)
            mediciones.terminar_asignacion(self.ac, asignacion)
        with self.como(self.gc):
            periodo = mediciones.abrir_periodo(self.gc, "2026-B", date(2026, 7, 1), date(2026, 12, 31))
        with self.como(self.tc):  # bloquea el período y la parcela, que no puede cambiar
            medicion = mediciones.crear_medicion(self.tc, parcela, self.periodo, **DATOS)
        self.assertEqual((medicion.autor, ParcelaVersion.objects.filter(parcela=parcela).count()), (self.tc, 2))
        self.assertEqual(Periodo.objects.get(pk=periodo.pk).abierto_por, self.gc)
        with self.como(self.pf):  # el PEP lee sus atributos bajo RLS
            self.assertEqual(set(listar(self.pf, "fincas").values_list("pk", flat=True)), {self.finca.pk})
            cliente.autorizar(self.pf, Accion.FINCA_LEER, self.finca)

    def test_solo_ac_escribe_el_catalogo_y_las_asignaciones(self):
        finca = self.finca.pk
        with self.como(self.gc):
            self.assertIsInstance(self.sql("UPDATE catalogo_finca SET nombre = 'x' WHERE id = %s", [finca]),
                                  DatabaseError)
            self.assertIsInstance(self.sql(
                "INSERT INTO catalogo_finca (nombre, ubicacion, estado, propietario_id, creada_por_id, creada_en) "
                "VALUES ('x', 'y', 'ACTIVA', %s, %s, now())", [self.pf.pk, self.gc.pk]), DatabaseError)
            self.assertIsInstance(self.sql("UPDATE mediciones_asignacion SET vigente_hasta = now()"), DatabaseError)
        with self.como(self.pf):
            self.assertIsInstance(self.sql("UPDATE catalogo_finca SET nombre = 'mía' WHERE id = %s", [finca]),
                                  DatabaseError)
            self.assertEqual(self.sql("UPDATE catalogo_datospropietario SET telefono = '1'"), 0)
        with self.como(self.tc2):
            self.assertEqual(self.sql("UPDATE catalogo_finca SET nombre = 'x' WHERE id = %s", [finca]), 0)  # no la ve
        with self.como(self.ac):
            self.assertIsInstance(self.sql(
                "INSERT INTO catalogo_finca (nombre, ubicacion, estado, propietario_id, creada_por_id, creada_en) "
                "VALUES ('x', 'y', 'ACTIVA', %s, %s, now())", [self.pf.pk, self.gc.pk]), DatabaseError)  # a nombre de otro
            alta = self.sql(
                "INSERT INTO catalogo_finca (nombre, ubicacion, estado, propietario_id, creada_por_id, creada_en) "
                "VALUES ('x', 'y', 'ACTIVA', %s, %s, now())", [self.pf.pk, self.ac.pk])
            self.assertEqual(alta, 1)                                            # la misma, a su nombre, sí

    def test_solo_gc_cambia_periodos(self):
        for usuario in (self.tc, self.vf, self.ac):
            with self.subTest(usuario=usuario.username), self.como(usuario):
                self.assertIsInstance(self.sql("UPDATE mediciones_periodo SET estado = 'CERRADO'"), DatabaseError)
        with self.como(self.gc):
            self.assertEqual(self.sql("UPDATE mediciones_periodo SET nombre = nombre"), 1)

    def test_nadie_borra(self):
        for usuario in (self.ac, self.gc):
            with self.como(usuario):
                for tabla in ("catalogo_finca", "catalogo_parcela", "catalogo_parcelaversion",
                              "catalogo_datospropietario", "mediciones_periodo", "mediciones_asignacion"):
                    with self.subTest(usuario=usuario.username, tabla=tabla):
                        self.assertEqual(self.sql(f"DELETE FROM {tabla}"), 0)


class PEPFallaCerradaTests(RLSDominioBase):
    def test_sin_atributos_se_niega(self):
        with mock.patch.object(pep, "construir_solicitud", side_effect=Finca.DoesNotExist), \
                self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.gc, Accion.FINCA_LEER, self.finca)
