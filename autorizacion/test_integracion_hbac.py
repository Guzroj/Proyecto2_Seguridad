"""
Paso 15 · HBAC de punta a punta: el PEP cuenta en la base, el motor real decide
y Django anota la actividad y crea las alertas. Incluye V11, V13 y la RLS de
los contadores.
"""

import unittest

try:
    import django  # noqa: F401
except ImportError:  # imagen del autorizador: solo corre las pruebas puras del motor
    raise unittest.SkipTest("Django no está instalado en esta imagen")

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import DatabaseError, connection, transaction
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from autorizacion import cliente, pep
from autorizacion.test_integracion_o7_o9 import MotorRealBase
from catalogo import services as catalogo
from cuentas import hbac, rls, selectores, services
from cuentas.models import ActividadHBAC, Alerta, ControlHBAC, EstadoAlerta, PrioridadAlerta, TipoActividad
from cuentas.selectores import hoy
from cuentas.test_gestion import ultimo
from cuentas.test_rls import ROL, RLSBase
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, ReautenticacionRequerida

CR = ZoneInfo("America/Costa_Rica")
L, X = TipoActividad.LECTURA, TipoActividad.EXPORTACION


def anotar(usuario, cuantas, *, hace, tipo=L, objeto="O4", registros=1):
    """Historial armado a mano: `cuantas` filas en el instante ahora − `hace`."""
    en = timezone.now() - hace
    ActividadHBAC.objects.bulk_create(
        ActividadHBAC(sujeto=usuario, tipo=tipo, objeto=objeto, registros=registros, en=en) for _ in range(cuantas))


class HBACBase(MotorRealBase):
    def setUp(self):
        super().setUp()
        # En horario salvo que la prueba fije la hora (a_las): el resultado no depende del día real.
        self.en_horario = mock.patch("autorizacion.hbac.fuera_de_horario", return_value=False)
        self.en_horario.start()
        self.addCleanup(mock.patch.stopall)

    def con_mfa(self, usuario, edad=10.0):
        """Simula la petición de `usuario` con segundo factor de hace `edad` s."""
        marca = pep.peticion_actual.set(SimpleNamespace(user=usuario, session={}))
        self.addCleanup(pep.peticion_actual.reset, marca)
        parche = mock.patch("cuentas.mfa.edad_mfa", return_value=edad)
        parche.start()
        self.addCleanup(parche.stop)

    def a_las(self, *fecha):
        """Fija el reloj del servidor en hora de Costa Rica (y deja que el motor evalúe el horario)."""
        self.en_horario.stop()
        self.en_horario = mock.patch.object(pep, "reloj", return_value=datetime(*fecha, tzinfo=CR))
        self.en_horario.start()

    def lecturas(self, usuario):
        return ActividadHBAC.objects.filter(sujeto=usuario, tipo=L).count()


class SelectoresTests(HBACBase):
    def test_n_cuenta_las_lecturas_propias_de_la_ultima_hora(self):
        anotar(self.g1, 3, hace=timedelta(minutes=10))
        anotar(self.g1, 4, hace=timedelta(minutes=70))
        anotar(self.g1, 1, hace=timedelta(minutes=5), tipo=X, registros=9)
        anotar(self.g2, 5, hace=timedelta(minutes=5))
        self.assertEqual(selectores.lecturas_h1(self.g1), 3)

    def test_media_propia_solo_con_90_dias_de_historial(self):
        anotar(self.g1, 30, hace=timedelta(days=2))
        self.assertIsNone(selectores.media_propia_h1(self.g1))
        anotar(self.g1, 1, hace=timedelta(days=91))
        anotar(self.g1, 10, hace=timedelta(days=3))
        anotar(self.g1, 50, hace=timedelta(minutes=20))  # la hora que mide n(s) no cuenta
        self.assertEqual(selectores.media_propia_h1(self.g1), 20.0)  # 40 lecturas en 2 horas activas

    def test_media_del_rol(self):
        anotar(self.g1, 10, hace=timedelta(days=1))
        anotar(self.g1, 20, hace=timedelta(days=2))
        anotar(self.g2, 3, hace=timedelta(days=1))
        anotar(self.tc, 100, hace=timedelta(days=1))
        self.assertAlmostEqual(selectores.media_rol_h1("GC"), 33 / 3)
        self.assertEqual(selectores.media_rol_h1("TC"), 100.0)
        self.assertIsNone(selectores.media_rol_h1("VF"))

    def test_exportados_en_24_h(self):
        anotar(self.g1, 2, hace=timedelta(hours=3), tipo=X, registros=300)
        anotar(self.g1, 1, hace=timedelta(hours=25), tipo=X, registros=900)
        self.assertEqual(selectores.exportados_h3(self.g1), 600)


class H1Tests(HBACBase):
    def test_cada_lectura_concedida_de_o1_y_o4_se_anota(self):
        cliente.autorizar(self.g1, Accion.FINCA_LEER)
        cliente.autorizar(self.g1, Accion.MEDICION_LEER)
        cliente.autorizar(self.g1, Accion.PERIODO_LEER)                  # O2: no cuenta
        with self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.pf, Accion.MEDICION_EXPORTAR)          # negada: no cuenta
        self.assertEqual(sorted(ActividadHBAC.objects.values_list("objeto", flat=True)), ["O1a", "O4"])

    def resolver_hace(self, alerta, minutos):
        """GC distinto resuelve; la resolución se mueve `minutos` al pasado (solo 'sistema' puede)."""
        services.resolver_alerta(self.g2, alerta, motivo="Revisión de cierre de período")
        rls.fijar_contexto(fase="sistema")
        Alerta.objects.filter(pk=alerta.pk).update(resuelta_en=timezone.now() - timedelta(minutes=minutos))
        rls.fijar_contexto()

    def test_secuencia_25_lecturas_la_26_niega_otro_gc_resuelve_y_se_pide_mfa(self):
        for _ in range(25):
            cliente.autorizar(self.g1, Accion.FINCA_LEER)
        with self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.g1, Accion.FINCA_LEER)
        alerta = Alerta.objects.get(sujeto=self.g1, control=ControlHBAC.H1)
        self.assertEqual((alerta.estado, alerta.prioridad), (EstadoAlerta.ABIERTA, PrioridadAlerta.ALTA))
        self.assertEqual((alerta.detalle["n"], alerta.detalle["umbral"]), (26, 25))
        self.assertEqual(self.lecturas(self.g1), 25)                       # la negada no se anota

        with self.assertRaises(AccesoDenegado):                            # suspendido
            cliente.autorizar(self.g1, Accion.MEDICION_LEER)
        cliente.autorizar(self.g1, Accion.PERIODO_LEER)                     # lo demás sigue

        services.resolver_alerta(self.g2, alerta, motivo="Revisión de cierre de período")
        self.assertEqual(selectores.lecturas_h1(self.g1), 0)                # las 25 de antes ya no cuentan
        with self.assertRaises(ReautenticacionRequerida):                   # y no AccesoDenegado
            cliente.autorizar(self.g1, Accion.FINCA_LEER)
        self.assertEqual(Alerta.objects.filter(sujeto=self.g1, control=ControlHBAC.H1).count(), 1)
        self.assertFalse(selectores.suspendido_h1(self.g1))

    def test_tras_resolver_cuenta_desde_la_resolucion_y_se_dispara_de_nuevo(self):
        anotar(self.g1, 25, hace=timedelta(minutes=5))
        with self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.g1, Accion.FINCA_LEER)
        self.resolver_hace(Alerta.objects.get(sujeto=self.g1, control=ControlHBAC.H1), minutos=1)
        self.con_mfa(self.g1, edad=10.0)                                    # segundo factor posterior
        for _ in range(25):
            cliente.autorizar(self.g1, Accion.FINCA_LEER)                   # n = 1 … 25 desde la resolución
        with self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.g1, Accion.FINCA_LEER)                   # n = 26: H1 otra vez
        h1 = Alerta.objects.filter(sujeto=self.g1, control=ControlHBAC.H1)
        self.assertEqual(sorted(h1.values_list("estado", flat=True)), [EstadoAlerta.ABIERTA, EstadoAlerta.RESUELTA])
        self.assertEqual(h1.get(estado=EstadoAlerta.ABIERTA).detalle["n"], 26)

    def test_la_suspension_es_de_la_persona_tambien_como_pf(self):
        services.agregar_rol(self.tc, "PF")                                 # TC + PF
        propia = catalogo.crear_finca(self.ac, "La Esperanza", "San Carlos", self.tc)
        de_pf = catalogo.crear_finca(self.ac, "El Roble", "Sarapiquí", self.pf)
        cliente.autorizar(self.tc, Accion.FINCA_LEER, propia)               # concede como PF (C4)
        cliente.autorizar(self.pf, Accion.FINCA_LEER, de_pf)
        services.crear_alerta(self.tc, ControlHBAC.H1)
        services.crear_alerta(self.pf, ControlHBAC.H1)
        for usuario, finca in ((self.tc, propia), (self.pf, de_pf)):
            with self.subTest(usuario=usuario.username), self.assertRaises(AccesoDenegado):
                cliente.autorizar(usuario, Accion.FINCA_LEER, finca)
        self.assertEqual(ultimo("alerta:crear")["despues"]["control"], "H1")

    def test_v11_gc_lee_a_mas_del_triple_de_su_media(self):
        anotar(self.g1, 1, hace=timedelta(days=91))
        anotar(self.g1, 20, hace=timedelta(days=5))
        anotar(self.g1, 20, hace=timedelta(days=6))                         # μ = 20 → umbral 60
        anotar(self.g1, 59, hace=timedelta(minutes=30))
        cliente.autorizar(self.g1, Accion.MEDICION_LEER)                     # n = 60
        with self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.g1, Accion.MEDICION_LEER)                 # n = 61
        self.assertEqual(Alerta.objects.get(sujeto=self.g1, control=ControlHBAC.H1).detalle["media"], 20.0)

    def test_dentro_de_una_peticion_la_alerta_sobrevive_al_rollback(self):
        anotar(self.g1, 25, hace=timedelta(minutes=5))

        def vista(request):
            try:
                with transaction.atomic():
                    cliente.autorizar(self.g1, Accion.FINCA_LEER)
            except AccesoDenegado:
                self.assertFalse(Alerta.objects.filter(sujeto=self.g1).exists())  # aún pendiente
                return HttpResponse(status=403)
            return HttpResponse()

        peticion = RequestFactory().get("/")
        peticion.user, peticion.session = self.g1, {}
        self.assertEqual(pep.PeticionActualMiddleware(vista)(peticion).status_code, 403)
        self.assertTrue(selectores.suspendido_h1(self.g1))

    def test_si_no_se_puede_anotar_se_niega(self):
        with mock.patch.object(services, "registrar_actividad", side_effect=DatabaseError("sin espacio")), \
                self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.g1, Accion.FINCA_LEER)


class H2Tests(HBACBase):
    def test_fuera_de_horario_concede_y_alerta(self):
        self.a_las(2026, 11, 15, 9, 0)                                       # domingo
        cliente.autorizar(self.g1, Accion.FINCA_LEER)
        cliente.autorizar(self.g1, Accion.MEDICION_LEER)
        alerta = Alerta.objects.get(sujeto=self.g1, control=ControlHBAC.H2)  # agrupadas en una
        self.assertEqual(alerta.prioridad, PrioridadAlerta.ALTA)
        self.assertEqual(ultimo("alerta:crear")["despues"]["accion"], "medicion:leer")  # cada una a bitácora
        self.assertEqual(self.lecturas(self.g1), 2)

    def test_en_horario_o_sin_marca_no_alerta(self):
        self.a_las(2026, 11, 11, 10, 0)                                      # miércoles
        cliente.autorizar(self.g1, Accion.FINCA_LEER)
        self.a_las(2026, 11, 15, 3, 0)
        cliente.autorizar(self.g1, Accion.PERIODO_LEER)                      # O2 no tiene ‡
        self.assertFalse(Alerta.objects.exists())


class H3Tests(HBACBase):
    def setUp(self):
        super().setUp()
        self.ae = services.crear_cuenta(self.ac, username="auditora", rol="AE", inicio=hoy(),
                                        fin=hoy() + timedelta(days=5))
        services.aprobar_ventana(self.g1, self.ae.ventana_auditoria)

    def exportar(self, registros):
        cliente.autorizar(self.ae, Accion.MEDICION_EXPORTAR, None, {"registros": registros})

    def test_v13_exporta_2000_con_segundo_factor_reciente(self):
        with self.assertRaises(ReautenticacionRequerida):
            self.exportar(2000)
        self.assertFalse(ActividadHBAC.objects.exists())
        self.con_mfa(self.ae)
        self.exportar(2000)
        fila = ActividadHBAC.objects.get(sujeto=self.ae)
        self.assertEqual((fila.tipo, fila.objeto, fila.registros), (X, "O4", 2000))
        marca = hbac.marca_de_agua(self.ae)
        self.assertIn("auditora", marca)
        self.assertIn(timezone.localdate().isoformat(), marca)

    def test_se_acumula_en_24_h(self):
        self.exportar(400)
        with self.assertRaises(ReautenticacionRequerida):
            self.exportar(101)                                                # N = 501
        self.exportar(100)                                                    # N = 500
        self.assertEqual(selectores.exportados_h3(self.ae), 500)

    def test_fuera_de_horario_alerta(self):
        self.a_las(2026, 11, 14, 11, 0)                                       # sábado
        self.con_mfa(self.ae)
        self.exportar(800)
        alerta = Alerta.objects.get(sujeto=self.ae, control=ControlHBAC.H3)
        self.assertEqual(alerta.detalle, {"accion": "medicion:exportar", "registros": 800, "fuera_de_horario": True})

    def test_sin_numero_de_registros_se_niega(self):
        with self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.ae, Accion.MEDICION_EXPORTAR)


class RLSActividadTests(RLSBase):
    def setUp(self):
        super().setUp()
        with connection.cursor() as c:
            c.execute(f"GRANT EXECUTE ON FUNCTION cuentas_media_lecturas_rol(text, timestamptz, timestamptz) TO {ROL}")
        anotar(self.beto, 6, hace=timedelta(days=1))

    def test_cada_quien_ve_y_anota_solo_lo_suyo(self):
        with self.como_app(self.ana.pk):
            services.registrar_actividad(self.ana, L, "O1a")
            self.assertEqual(ActividadHBAC.objects.count(), 1)
            self.assertEqual(selectores.media_rol_h1("VF"), 6.0)           # solo el número, no las filas
            ajena = self.ajeno("INSERT INTO cuentas_actividadhbac (sujeto_id, tipo, objeto, registros, en) "
                               "VALUES (%s, 'L', 'O4', 1, now())", [self.beto.pk])
            self.assertIsInstance(ajena, DatabaseError)

    def test_nadie_se_fabrica_historial_ni_lo_cambia(self):
        with self.como_app(self.beto.pk):
            antigua = self.ajeno("INSERT INTO cuentas_actividadhbac (sujeto_id, tipo, objeto, registros, en) "
                                 "VALUES (%s, 'L', 'O4', 1, statement_timestamp() - interval '100 days')",
                                 [self.beto.pk])
            self.assertIsInstance(antigua, DatabaseError)
            self.assertEqual(self.ajeno("UPDATE cuentas_actividadhbac SET en = en - interval '100 days'", []), 0)
            self.assertEqual(self.ajeno("DELETE FROM cuentas_actividadhbac", []), 0)
        self.assertEqual(ActividadHBAC.objects.filter(sujeto=self.beto).count(), 6)
