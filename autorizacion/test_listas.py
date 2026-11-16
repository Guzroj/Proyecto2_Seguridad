"""
Paso 16 · Listas filtradas: cada sujeto ve en una lista exactamente lo que el
motor le deja leer objeto por objeto (p. ej., PF solo lo suyo).
"""

import unittest

try:
    import django  # noqa: F401
except ImportError:  # imagen del autorizador: solo corre las pruebas puras del motor
    raise unittest.SkipTest("Django no está instalado en esta imagen")

import os
from datetime import timedelta
from unittest import mock

from django.test import override_settings

from autorizacion import cliente, listas, pep, reglas
from autorizacion.listas import LISTAS, listar
from autorizacion.test_cliente import LOCAL, MotorFalso, con_rol
from autorizacion.test_integracion_o1_o3 import EscenarioBase, poligono
from autorizacion.test_integracion_o4_o6 import DATOS
from catalogo import services as catalogo
from catalogo.models import DatosPropietario
from cuentas import services as cuentas
from cuentas import sesion
from cuentas.models import ActividadHBAC, ControlHBAC, RestablecimientoFactor
from cuentas.selectores import hoy
from cuentas.test_dispositivos import ed25519_pem
from mediciones import services as mediciones
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, FueraDeAlcance

class ListasBase(EscenarioBase):
    """
    Además del escenario de O1–O3: la medición de tc aprobada por vf, una enmienda
    de tc, otra finca de pf2 con medición de tc2, datos de propietario, un AE con
    ventana aprobada y objetos de O7–O9.
    """

    def setUp(self):
        super().setUp()
        self.enviar()
        self.medicion = mediciones.aprobar_medicion(self.vf, self.medicion)
        self.enmienda = mediciones.solicitar_enmienda(self.tc, self.medicion, justificacion="Conteo", **DATOS)
        self.finca2 = catalogo.crear_finca(self.ac, "El Roble", "Sarapiquí", self.pf2)
        self.parcela2 = catalogo.crear_parcela(self.ac, self.finca2, codigo="R1", area_ha="2", geometria=poligono(1))
        mediciones.crear_asignacion(self.ac, self.tc2, self.finca2, self.periodo)
        self.medicion2 = mediciones.crear_medicion(self.tc2, self.parcela2, self.periodo, **DATOS)
        for pf in (self.pf, self.pf2):
            catalogo.registrar_datos_propietario(self.ac, pf, nombre_completo=f"Nombre {pf.pk}", identificacion=f"1-{pf.pk}")
        self.ae = cuentas.crear_cuenta(self.ac, username="auditora", rol="AE", inicio=hoy(),
                                       fin=hoy() + timedelta(days=5))
        cuentas.aprobar_ventana(self.gc, self.ae.ventana_auditoria)
        self.mixto = con_rol("tecnica_duena", "TC", "PF")
        self.finca_mixta = catalogo.crear_finca(self.ac, "Mixta", "Upala", self.mixto)
        cuentas.registrar_dispositivo(self.ac, self.tc, etiqueta="Tableta", llave_publica=ed25519_pem())
        cuentas.solicitar_restablecimiento(self.ac, self.vf2, motivo="Perdió el teléfono")
        cuentas.asignar_rol(self.ac, self.pf2, "GC")
        cuentas.crear_alerta(self.tc2, ControlHBAC.H3)
        for usuario in (self.tc, self.gc):
            sesion.registrar_inicio(usuario, f"clave-{usuario.pk}")

        # Desde aquí, el motor real (por HTTP falso) y siempre en horario.
        for parche in (MotorFalso().instalar(), mock.patch.dict(os.environ, LOCAL),
                       override_settings(DEBUG=True),
                       mock.patch("autorizacion.hbac.fuera_de_horario", return_value=False)):
            parche.enable() if hasattr(parche, "enable") else parche.start()
            self.addCleanup(parche.disable if hasattr(parche, "disable") else parche.stop)
        self.usuarios = [self.ac, self.gc, self.tc, self.tc2, self.pf, self.pf2, self.vf, self.vf2, self.ae,
                         self.mixto]

    def ids(self, usuario, nombre):
        return set(listar(usuario, nombre).values_list("pk", flat=True))


class ConsistenciaTests(ListasBase):
    def test_cada_lista_coincide_con_la_lectura_objeto_por_objeto(self):
        revisados = 0
        for nombre, lista in LISTAS.items():
            leer = lista.accion.replace(":listar", ":leer")
            objetos = list(lista.queryset())
            self.assertTrue(objetos, f"El escenario no tiene {nombre}")
            for usuario in self.usuarios:
                try:
                    visibles = self.ids(usuario, nombre)
                except AccesoDenegado:
                    visibles = set()
                for objeto in objetos:
                    with self.subTest(lista=nombre, usuario=usuario.username, objeto=objeto.pk):
                        decision = reglas.decidir(pep.construir_solicitud(usuario, leer, objeto))
                        self.assertEqual(objeto.pk in visibles, decision.concede, decision.motivo)
                    revisados += 1
        self.assertGreater(revisados, 300)


class AlcanceTests(ListasBase):
    def test_pf_solo_ve_lo_suyo(self):
        self.assertEqual(self.ids(self.pf, "fincas"), {self.finca.pk})
        self.assertEqual(self.ids(self.pf, "parcelas"), {self.parcela.pk})
        self.assertEqual(self.ids(self.pf, "propietarios"), {DatosPropietario.objects.get(usuario=self.pf).pk})
        self.assertEqual(self.ids(self.pf, "mediciones"), {self.medicion.pk})     # solo APROBADA
        self.assertEqual(self.ids(self.pf2, "mediciones"), set())                  # la suya está en BORRADOR
        self.assertEqual(self.ids(self.pf, "cuentas"), {self.pf.pk})
        for nombre in ("periodos", "asignaciones", "enmiendas", "alertas", "roles"):
            with self.subTest(lista=nombre), self.assertRaises(AccesoDenegado):
                listar(self.pf, nombre)

    def test_tc_ve_lo_asignado_y_lo_suyo(self):
        self.assertEqual(self.ids(self.tc, "fincas"), {self.finca.pk})
        self.assertEqual(self.ids(self.tc2, "fincas"), {self.finca2.pk})            # V1: no ve la otra
        self.assertEqual(self.ids(self.tc, "asignaciones"), {self.asignacion.pk})
        self.assertEqual(self.ids(self.tc, "mediciones"),
                         {self.medicion.pk, self.enmienda.version_id} - {None})
        self.assertEqual(self.ids(self.tc, "enmiendas"), {self.enmienda.pk})
        self.assertEqual(self.ids(self.tc2, "enmiendas"), set())

    def test_vf_ve_lo_que_verifico(self):
        self.assertEqual(self.ids(self.vf, "fincas"), {self.finca.pk})
        self.assertIn(self.medicion.pk, self.ids(self.vf, "mediciones"))
        self.assertEqual(self.ids(self.vf2, "fincas"), set())

    def test_varios_roles_unen_su_alcance(self):
        self.assertEqual(self.ids(self.mixto, "fincas"), {self.finca_mixta.pk})     # PF: la suya; TC: ninguna
        mediciones.crear_asignacion(self.ac, self.mixto, self.finca2, self.periodo)
        self.assertEqual(self.ids(self.mixto, "fincas"), {self.finca_mixta.pk, self.finca2.pk})

    def test_gc_ac_y_ae_ven_toda_su_columna(self):
        self.assertEqual(listar(self.gc, "fincas").count(), 3)
        self.assertEqual(listar(self.ac, "propietarios").count(), 2)
        self.assertEqual(listar(self.ae, "alertas").count(), 1)
        with self.assertRaises(AccesoDenegado):
            listar(self.gc, "propietarios")                                         # GC no tiene O1b

    def test_ae_fuera_de_su_ventana_no_lista(self):
        with mock.patch("cuentas.selectores.hoy", return_value=hoy() + timedelta(days=6)), \
                self.assertRaises(AccesoDenegado):
            listar(self.ae, "mediciones")


class HBACListasTests(ListasBase):
    def test_una_lista_cuenta_como_una_lectura(self):
        ActividadHBAC.objects.all().delete()
        listar(self.gc, "fincas")
        listar(self.gc, "periodos")                                                 # O2: no cuenta
        self.assertEqual(list(ActividadHBAC.objects.values_list("sujeto_id", "objeto")), [(self.gc.pk, "O1a")])

    def test_suspendido_no_lista_o1_ni_o4(self):
        cuentas.crear_alerta(self.pf, ControlHBAC.H1)
        with self.assertRaises(AccesoDenegado):
            listar(self.pf, "fincas")
        self.assertEqual(self.ids(self.pf, "cuentas"), {self.pf.pk})                # O7a sigue

    def test_traduccion_faltante_no_deja_ver_nada(self):
        sin_c6 = listas.Lista("catalogo.Finca", "finca:listar", {"C4": lambda s: listas.Q(propietario_id=s)})
        self.assertEqual(listas.alcance_de_rol(sin_c6, "TC", self.tc.pk), listas.NADA)
        self.assertEqual(listas.alcance_de_rol(sin_c6, "GC", self.gc.pk), listas.Q())


class RestablecimientosGCTests(ListasBase):
    """Corrección 3: GC·O7d L acotada por C17."""

    def setUp(self):
        super().setUp()
        self.gc2 = con_rol("gerente2", "GC")
        self.del_vf2 = RestablecimientoFactor.objects.get(usuario=self.vf2)
        self.del_gc = cuentas.solicitar_restablecimiento(self.ac, self.gc, motivo="Cambió de teléfono")
        self.usuarios.append(self.gc2)

    def test_gc_lista_solo_lo_que_podria_resolver(self):
        self.assertEqual(self.ids(self.gc, "restablecimientos"), {self.del_vf2.pk})       # no el suyo
        self.assertEqual(self.ids(self.gc2, "restablecimientos"), {self.del_vf2.pk, self.del_gc.pk})
        cuentas.rechazar_restablecimiento(self.gc2, self.del_gc, motivo="Duplicado")
        self.assertEqual(self.ids(self.gc2, "restablecimientos"), {self.del_vf2.pk})      # resuelto: ya no
        self.assertEqual(len(self.ids(self.ac, "restablecimientos")), 2)                  # AC: todos

    def test_metadatos_solo_de_la_cuenta_en_restablecimiento(self):
        cliente.autorizar(self.gc, Accion.FACTOR_LEER, self.vf2)
        for persona in (self.tc, self.gc):                                                # sin restablecimiento / él mismo
            with self.subTest(persona=persona.username), self.assertRaises(FueraDeAlcance):
                cliente.autorizar(self.gc, Accion.FACTOR_LEER, persona)
        with self.assertRaises(FueraDeAlcance):
            cliente.autorizar(self.gc, Accion.FACTOR_LEER, self.del_gc)                   # el que es sobre él

    def test_consistencia_con_dos_gc(self):
        ConsistenciaTests.test_cada_lista_coincide_con_la_lectura_objeto_por_objeto(self)
