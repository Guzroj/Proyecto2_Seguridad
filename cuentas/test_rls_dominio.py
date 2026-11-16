"""
Paso 17 · RLS de las tablas del dominio, con un rol sin privilegios equivalente a
app_runtime (SET ROLE) y los servicios reales autorizando con el motor.

17a: catálogo (O1a, O1b), períodos (O2) y asignaciones (O3). Lectura por finca
relacionada; escritura solo de quien la matriz deja escribir; nadie borra.
17b: mediciones (O4), actores, enmiendas (O5), motivos y la vía de proceso del
certificado (O1b solo para el VF que aprueba, solo nombre e identificación).

Como en Neon, las funciones SECURITY DEFINER tienen un dueño sin privilegios de
superusuario, así que RLS también aplica dentro de ellas (y se prueba que las
políticas no se llaman a sí mismas sin fin).
"""

import unittest

try:
    import django  # noqa: F401
except ImportError:
    raise unittest.SkipTest("Django no está instalado en esta imagen")

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.db import DatabaseError, connection, transaction
from django.test import override_settings

from autorizacion import cliente, pep, procesos
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
from mediciones.models import Asignacion, Enmienda, Medicion, MedicionActor, MotivoDevolucion, Periodo
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, FueraDeAlcance


DUENO = "verdeazul_dueno_prueba"
DEFINIDAS = ("app_ve_finca(bigint)", "app_ve_parcela(bigint)", "certificado_datos_propietario(uuid)",
             "cuentas_media_lecturas_rol(text, timestamptz, timestamptz)")
EMITIDOS = []


def emitir_prueba(medicion):
    """on_medicion_aprobada de prueba: lo que haría Persona 4 al emitir el certificado."""
    EMITIDOS.append(procesos.datos_propietario_para_certificado(medicion))


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
            c.execute(f"GRANT EXECUTE ON FUNCTION certificado_datos_propietario(uuid) TO {ROL}")
            c.execute(f"CREATE ROLE {DUENO} NOLOGIN NOSUPERUSER NOBYPASSRLS")
            c.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {DUENO}")
            c.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {DUENO}")
            for funcion in DEFINIDAS:
                c.execute(f"ALTER FUNCTION {funcion} OWNER TO {DUENO}")

    @contextmanager
    def como(self, usuario=None, fase=None):
        """Como app_runtime en Neon: session_user = current_user = el rol de la app."""
        with connection.cursor() as c:
            c.execute(f"SET SESSION AUTHORIZATION {ROL}")
        rls.fijar_contexto(usuario_id=getattr(usuario, "pk", None), fase=fase)
        try:
            yield
        finally:
            with connection.cursor() as c:
                c.execute("RESET SESSION AUTHORIZATION")
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
                # Transición válida (el disparador de C13 la deja pasar): la niega RLS.
                self.assertIsInstance(self.sql("UPDATE mediciones_periodo SET estado = 'EN_VERIFICACION'"), DatabaseError)
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


# ---------------------------------------------------------------------------
# 17b · Mediciones, actores, enmiendas, motivos y certificado
# ---------------------------------------------------------------------------
class RLSMedicionesBase(RLSDominioBase):
    def setUp(self):
        super().setUp()
        EMITIDOS.clear()
        self.motivo = MotivoDevolucion.objects.create(codigo="conteo", descripcion="Conteo incompleto")
        parche = override_settings(VERDEAZUL_GANCHOS={**GANCHOS,
                                                      "on_medicion_aprobada": "cuentas.test_rls_dominio.emitir_prueba"})
        parche.enable()
        self.addCleanup(parche.disable)

    def con_mfa(self, usuario):
        marca = pep.peticion_actual.set(SimpleNamespace(user=usuario, session={}))
        self.addCleanup(pep.peticion_actual.reset, marca)
        parche = mock.patch("cuentas.mfa.edad_mfa", return_value=10.0)
        parche.start()
        self.addCleanup(parche.stop)


class LecturaMedicionesTests(RLSMedicionesBase):
    def test_por_finca_relacionada(self):
        de_finca = set(Medicion.objects.filter(parcela__finca=self.finca).values_list("pk", flat=True))
        de_finca2 = {self.medicion2.pk}
        casos = [(self.pf, de_finca), (self.tc, de_finca), (self.vf, de_finca), (self.tc2, de_finca2),
                 (self.pf2, de_finca2), (self.vf2, set()), (self.mixto, set()),
                 (self.gc, de_finca | de_finca2), (self.ac, de_finca | de_finca2), (self.ae, de_finca | de_finca2)]
        for usuario, esperadas in casos:
            with self.subTest(usuario=usuario.username):
                self.assertEqual(self.visibles(Medicion, usuario), esperadas)
                actores = set(MedicionActor.objects.filter(medicion_id__in=esperadas).values_list("pk", flat=True))
                self.assertEqual(self.visibles(MedicionActor, usuario), actores)
                enmiendas = set(Enmienda.objects.filter(medicion_original_id__in=esperadas)
                                .values_list("pk", flat=True))
                self.assertEqual(self.visibles(Enmienda, usuario), enmiendas)

    def test_anonimo_y_preingreso_no_ven_nada(self):
        for modelo in (Medicion, MedicionActor, Enmienda, MotivoDevolucion):
            with self.subTest(modelo=modelo.__name__):
                self.assertEqual(self.visibles(modelo), set())
                self.assertEqual(self.visibles(modelo, self.gc, fase=FASE_PREINGRESO), set())
        # Un autenticado ve el catálogo completo (los motivos de la migración 0002 más el de la prueba).
        catalogo = set(MotivoDevolucion.objects.values_list("pk", flat=True))
        self.assertIn(self.motivo.pk, catalogo)
        self.assertEqual(self.visibles(MotivoDevolucion, self.vf), catalogo)


class FlujoMedicionesTests(RLSMedicionesBase):
    def test_enmienda_autorizada_y_version_aprobada_con_certificado(self):
        enmienda = Enmienda.objects.get(pk=self.enmienda.pk)
        with self.como(self.gc):
            self.con_mfa(self.gc)
            enmienda = mediciones.autorizar_enmienda(self.gc, enmienda)
        version = Medicion.objects.get(pk=enmienda.version_id)
        self.assertEqual((version.autor, version.verificador), (self.tc, self.vf2))
        self.assertTrue(MedicionActor.objects.filter(medicion=version, accion="HEREDADO").exists())
        with self.como(self.vf2):
            self.con_mfa(self.vf2)
            self.assertEqual(DatosPropietario.objects.count(), 0)                 # VF no tiene O1b
            mediciones.aprobar_medicion(self.vf2, version)
        self.assertEqual(Enmienda.objects.get(pk=enmienda.pk).estado, "APLICADA")
        propios = DatosPropietario.objects.get(usuario=self.pf)
        self.assertEqual(EMITIDOS, [{"nombre_completo": propios.nombre_completo,
                                     "identificacion": propios.identificacion}])

    def test_crear_enviar_y_devolver(self):
        with self.como(self.ac):
            parcela = catalogo.crear_parcela(self.ac, self.finca, codigo="P4", area_ha="1", geometria=POLIGONO)
        with self.como(self.tc):
            m = mediciones.crear_medicion(self.tc, parcela, self.periodo, **DATOS)
            m = mediciones.modificar_medicion(self.tc, m, numero_arboles=140)
            with mock.patch("mediciones.services._azar.choice", side_effect=lambda c: next(u for u in c if u == self.vf)):
                m = mediciones.enviar_medicion(self.tc, m)
        with self.como(self.vf):
            self.con_mfa(self.vf)
            m = mediciones.devolver_medicion(self.vf, m, self.motivo)
        self.assertEqual((m.estado, m.verificador), ("DEVUELTA", self.vf))


class EscrituraMedicionesTests(RLSMedicionesBase):
    def test_solo_autor_verificador_o_gc_en_su_papel(self):
        # RLS, sobre una medición en BORRADOR (medicion2, de tc2): los disparadores dejan editarla,
        # así que quien la rechaza es RLS.
        m2 = str(self.medicion2.pk)
        cambio = "UPDATE mediciones_medicion SET observaciones = 'x' WHERE id = %s"
        for ajeno in (self.tc, self.vf2):
            with self.subTest(usuario=ajeno.username), self.como(ajeno):
                self.assertEqual(self.sql(cambio, [m2]), 0)
        with self.como(self.gc):
            self.assertIsInstance(self.sql(cambio, [m2]), DatabaseError)
        # PF no lee períodos (sin O2): el disparador de H4 no puede comprobarlo y niega
        # antes que RLS (fallo cerrado). Lo que importa es que se rechaza.
        with self.como(self.pf2), self.assertRaises(DatabaseError):
            with transaction.atomic(), connection.cursor() as c:
                c.execute(cambio, [m2])
        with self.como(self.tc2):                                               # su autor sí
            self.assertEqual(self.sql(cambio, [m2]), 1)
        # H4 en la base: una medición APROBADA no la modifica nadie, ni su verificador.
        with self.como(self.vf), self.assertRaisesRegex(DatabaseError, "H4"):
            with transaction.atomic(), connection.cursor() as c:
                c.execute(cambio, [str(self.medicion.pk)])

    def test_altas_a_nombre_propio(self):
        m = str(self.medicion.pk)
        with self.como(self.tc):
            self.assertIsInstance(self.sql(
                "INSERT INTO mediciones_medicionactor (medicion_id, usuario_id, accion, fecha) "
                "VALUES (%s, %s, 'APROBAR', now())", [m, self.vf2.pk]), DatabaseError)
            self.assertIsInstance(self.sql(
                "INSERT INTO mediciones_enmienda (medicion_original_id, solicitante_id, justificacion, estado, "
                "solicitada_en, numero_arboles, dap_promedio_cm, altura_promedio_m, observaciones) "
                "VALUES (%s, %s, 'x', 'SOLICITADA', now(), 1, 1, 1, '')", [m, self.tc2.pk]), DatabaseError)
        with self.como(self.gc):                                                # GC no crea originales
            self.assertIsInstance(self.sql(
                "INSERT INTO mediciones_medicion (id, parcela_id, parcela_version_id, periodo_id, autor_id, estado, "
                "numero_arboles, dap_promedio_cm, altura_promedio_m, observaciones, toneladas_co2, creada_en, "
                "comentario_devolucion) "
                "SELECT gen_random_uuid(), parcela_id, parcela_version_id, periodo_id, autor_id, 'BORRADOR', "
                "numero_arboles, dap_promedio_cm, altura_promedio_m, '', toneladas_co2, now(), '' "
                "FROM mediciones_medicion WHERE id = %s", [m]), DatabaseError)
        with self.como(self.vf):
            self.assertEqual(self.sql("UPDATE mediciones_motivodevolucion SET activo = false"), 0)  # catálogo cerrado

    def test_nadie_borra(self):
        for usuario in (self.tc, self.vf, self.gc):
            with self.como(usuario):
                for tabla in ("mediciones_medicion", "mediciones_medicionactor", "mediciones_enmienda",
                              "mediciones_motivodevolucion"):
                    with self.subTest(usuario=usuario.username, tabla=tabla):
                        self.assertEqual(self.sql(f"DELETE FROM {tabla}"), 0)


class CertificadoTests(RLSMedicionesBase):
    def test_solo_el_verificador_de_una_aprobada(self):
        propios = DatosPropietario.objects.get(usuario=self.pf)
        with self.como(self.vf):
            self.assertEqual(procesos.datos_propietario_para_certificado(self.medicion),
                             {"nombre_completo": propios.nombre_completo, "identificacion": propios.identificacion})
            with self.assertRaises(FueraDeAlcance):
                procesos.datos_propietario_para_certificado(self.medicion2)   # no aprobada ni suya
        for usuario in (self.tc, self.vf2, self.gc, self.pf2):
            with self.subTest(usuario=usuario.username), self.como(usuario), self.assertRaises(FueraDeAlcance):
                procesos.datos_propietario_para_certificado(self.medicion)

    def test_las_fases_internas_no_abren_nada_desde_afuera(self):
        # Solo valen dentro de las funciones (current_user = su dueño ≠ session_user):
        # fijadas por la app, el sujeto ve exactamente lo mismo que sin ellas.
        for fase in ("certificado", "ve_finca"):
            with self.subTest(fase=fase):
                for modelo in (DatosPropietario, Finca, Medicion, Asignacion, Enmienda):
                    self.assertEqual(self.visibles(modelo, self.tc2, fase=fase), self.visibles(modelo, self.tc2))
                self.assertEqual(self.visibles(DatosPropietario, self.tc2, fase=fase), set())
                self.assertEqual(self.visibles(Finca, self.tc2, fase=fase), {self.finca2.pk})
