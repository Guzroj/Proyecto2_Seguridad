"""
Alertas (O9): el motor HBAC las crea (proceso) y un GC distinto del
sujeto las resuelve con motivo (C22). Resolver una H1 levanta la suspensión y
la primera lectura posterior exige segundo factor. Incluye V14.
"""

import time
from datetime import timedelta
from types import SimpleNamespace

from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import override_settings
from django.utils import timezone

from cuentas import mfa, rls, services
from cuentas.models import Alerta, ControlHBAC, EstadoAlerta, PrioridadAlerta
from cuentas.selectores import suspendido_h1, ultima_resolucion_h1
from cuentas.test_gestion import EVENTOS, PERMISIVO, GestionBase, ultimo
from cuentas.test_rls import RLSBase
from cuentas.test_roles_c7 import con_rol
from nucleo.errores import AccesoDenegado, DatoInvalido, SeparacionDeFunciones, TransicionInvalida

H1, H2, H3 = ControlHBAC.H1, ControlHBAC.H2, ControlHBAC.H3


def envejecer(alerta, minutos):
    """Mueve creada_en al pasado (solo la fase 'sistema' puede tocar ese campo)."""
    rls.fijar_contexto(fase="sistema")
    Alerta.objects.filter(pk=alerta.pk).update(creada_en=timezone.now() - timedelta(minutes=minutos))
    rls.fijar_contexto()


class AlertaBase(GestionBase):
    def setUp(self):
        super().setUp()
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")
        self.vf = con_rol("verificador", "VF")

    def resolver(self, alerta, por=None, motivo="Revisado con la persona"):
        return services.resolver_alerta(por or self.g1, alerta, motivo=motivo)


class CrearAlertaTests(AlertaBase):
    def test_h1_es_bloqueante_y_de_prioridad_alta(self):
        alerta, creada = services.crear_alerta(self.vf, H1, detalle={"accion": "medicion:leer", "n": 40, "umbral": 25})
        self.assertTrue(creada)
        self.assertEqual((alerta.estado, alerta.prioridad, alerta.bloqueante),
                         (EstadoAlerta.ABIERTA, PrioridadAlerta.ALTA, True))
        evento = ultimo("alerta:crear")
        self.assertIsNone(evento["actor"])
        self.assertEqual((evento["despues"]["control"], evento["despues"]["n"]), ("H1", 40))

    def test_h1_no_se_duplica_mientras_esta_abierta(self):
        primera, _ = services.crear_alerta(self.vf, H1)
        segunda, creada = services.crear_alerta(self.vf, H1)
        self.assertEqual((segunda, creada), (primera, False))
        self.assertTrue(ultimo("alerta:crear")["despues"]["repetida"])
        self.resolver(primera)
        tercera, creada = services.crear_alerta(self.vf, H1)
        self.assertTrue(creada)
        self.assertNotEqual(tercera, primera)

    def test_h2_y_h3_se_agrupan_por_hora(self):
        for control, prioridad in ((H2, PrioridadAlerta.ALTA), (H3, PrioridadAlerta.NORMAL)):
            with self.subTest(control=control):
                a, _ = services.crear_alerta(self.vf, control, detalle={"fuera_de_horario": True})
                self.assertEqual(a.prioridad, prioridad)
                self.assertEqual(services.crear_alerta(self.vf, control)[0], a)
                envejecer(a, 61)
                self.assertTrue(services.crear_alerta(self.vf, control)[1])

    def test_por_sujeto(self):
        services.crear_alerta(self.vf, H1)
        self.assertTrue(services.crear_alerta(self.g1, H1)[1])

    def test_detalle_en_lista_blanca(self):
        for detalle in ({"nombre": "Rosa"}, {"n": [1, 2]}, {"accion": "x" * 101}, {"n": None}, "texto"):
            with self.subTest(detalle=detalle), self.assertRaises(DatoInvalido):
                services.crear_alerta(self.vf, H2, detalle=detalle)
        with self.assertRaises(DatoInvalido):
            services.crear_alerta(self.vf, "H9")
        self.assertFalse(Alerta.objects.exists())


class ResolverTests(AlertaBase):
    def test_resolver_h1_levanta_la_suspension(self):
        alerta, _ = services.crear_alerta(self.vf, H1)
        self.assertTrue(suspendido_h1(self.vf))
        self.resolver(alerta, motivo=" Carga legítima de fin de período ")
        alerta.refresh_from_db()
        self.assertEqual((alerta.estado, alerta.resuelta_por, alerta.motivo_resolucion),
                         (EstadoAlerta.RESUELTA, self.g1, "Carga legítima de fin de período"))
        self.assertFalse(suspendido_h1(self.vf))
        self.assertEqual(ultima_resolucion_h1(self.vf), alerta.resuelta_en)
        self.assertTrue(ultimo("alerta:resolver")["despues"]["levanta_suspension"])

    def test_v14_nadie_resuelve_una_alerta_sobre_si_mismo(self):
        alerta, _ = services.crear_alerta(self.g1, H2)
        with self.assertRaises(SeparacionDeFunciones):
            self.resolver(alerta, por=self.g1)
        self.assertEqual(ultimo("alerta:resolver")["resultado"], "DENEGADO")
        self.resolver(alerta, por=self.g2)

    def test_motivo_requerido(self):
        alerta, _ = services.crear_alerta(self.vf, H2)
        for motivo in ("", "  ", None, "x" * 501):
            with self.subTest(motivo=motivo), self.assertRaises(DatoInvalido):
                self.resolver(alerta, motivo=motivo)

    def test_se_resuelve_una_vez(self):
        alerta, _ = services.crear_alerta(self.vf, H2)
        self.resolver(alerta)
        with self.assertRaises(TransicionInvalida):
            self.resolver(alerta, por=self.g2)

    def test_h2_no_suspende(self):
        services.crear_alerta(self.vf, H2)
        services.crear_alerta(self.vf, H3)
        self.assertFalse(suspendido_h1(self.vf))

    @override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=False)
    def test_sin_motor_se_niega(self):
        alerta, _ = services.crear_alerta(self.vf, H1)
        with self.assertRaises(AccesoDenegado):
            self.resolver(alerta)
        self.assertTrue(suspendido_h1(self.vf))


class MFATrasH1Tests(AlertaBase):
    def peticion(self, *momentos_mfa):
        registros = [{"method": "password", "at": time.time() - 3600}]
        registros += [{"method": "mfa", "at": m} for m in momentos_mfa]
        return SimpleNamespace(user=self.vf, session={AUTHENTICATION_METHODS_SESSION_KEY: registros})

    def test_sin_suspensiones_no_exige(self):
        self.assertFalse(mfa.exige_mfa_tras_h1(self.peticion()))

    def test_exige_mfa_posterior_a_la_resolucion(self):
        antes = time.time() - 60
        alerta, _ = services.crear_alerta(self.vf, H1)
        self.resolver(alerta)
        self.assertTrue(mfa.exige_mfa_tras_h1(self.peticion()))          # nunca hizo mfa
        self.assertTrue(mfa.exige_mfa_tras_h1(self.peticion(antes)))     # mfa anterior a la resolución
        self.assertFalse(mfa.exige_mfa_tras_h1(self.peticion(antes, time.time() + 1)))  # ya se reautenticó


class RestriccionesDeLaBaseTests(AlertaBase):
    def crear(self, **datos):
        with transaction.atomic():
            return Alerta.objects.create(**{"sujeto": self.vf, "control": H2, "prioridad": "ALTA", **datos})

    def test_resolucion_coherente(self):
        ahora = timezone.now()
        for datos in ({"estado": "RESUELTA", "resuelta_por": self.g1, "resuelta_en": ahora},   # sin motivo
                      {"estado": "RESUELTA", "motivo_resolucion": "x"},                         # sin quién
                      {"motivo_resolucion": "x"}):                                              # abierta con motivo
            with self.subTest(datos=list(datos)), self.assertRaises(IntegrityError):
                self.crear(**datos)

    def test_el_sujeto_no_la_resuelve(self):
        with self.assertRaises(IntegrityError):
            self.crear(estado="RESUELTA", resuelta_por=self.vf, resuelta_en=timezone.now(), motivo_resolucion="x")

    def test_una_h1_abierta_por_sujeto(self):
        self.crear(control=H1)
        with self.assertRaises(IntegrityError):
            self.crear(control=H1)


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------
@PERMISIVO
class AlertasRLSTests(RLSBase):
    def setUp(self):
        super().setUp()
        EVENTOS.clear()
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")
        self.ac = con_rol("admin_ac", "AC")
        self.ae = con_rol("auditora", "AE")

    def alerta_de(self, sujeto, control=H1):
        with self.como_app(usuario_id=sujeto.pk):
            return services.crear_alerta(sujeto, control)[0]

    def test_el_motor_crea_en_la_peticion_del_sujeto(self):
        alerta = self.alerta_de(self.beto)
        with self.como_app(usuario_id=self.beto.pk):
            self.assertTrue(suspendido_h1(self.beto))
            self.assertEqual(Alerta.objects.get(), alerta)

    def test_nadie_crea_alertas_sobre_otra_persona(self):
        insertar = ("INSERT INTO cuentas_alerta (sujeto_id, control, prioridad, estado, detalle, creada_en,"
                    " motivo_resolucion) VALUES (%s, 'H1', 'ALTA', 'ABIERTA', '{}', now(), '')")
        for sujeto in (self.g1, self.beto):
            with self.subTest(sujeto=sujeto.username), self.como_app(usuario_id=sujeto.pk):
                self.assertIsInstance(self.ajeno(insertar, [self.ana.pk]), DatabaseError)
        with self.como_app(usuario_id=self.beto.pk, fase="preingreso"):
            self.assertIsInstance(self.ajeno(insertar, [self.beto.pk]), DatabaseError)

    def test_quien_ve_las_alertas(self):
        self.alerta_de(self.beto)
        self.alerta_de(self.ana, H2)
        vistos = {self.g1.pk: 2, self.ae.pk: 2, self.beto.pk: 1, self.ana.pk: 1, self.ac.pk: 0}
        for sujeto, esperado in vistos.items():
            with self.subTest(sujeto=sujeto), self.como_app(usuario_id=sujeto):
                self.assertEqual(Alerta.objects.count(), esperado)

    def test_solo_un_gc_distinto_resuelve(self):
        alerta = self.alerta_de(self.g1, H2)
        resolver = ("UPDATE cuentas_alerta SET estado = 'RESUELTA', resuelta_por_id = %s, resuelta_en = now(),"
                    " motivo_resolucion = 'x' WHERE id = %s")
        for sujeto in (self.g1, self.ac):
            with self.subTest(sujeto=sujeto.username), self.como_app(usuario_id=sujeto.pk):
                self.assertEqual(self.ajeno(resolver, [sujeto.pk, alerta.pk]), 0)
        with self.como_app(usuario_id=self.g2.pk):
            services.resolver_alerta(self.g2, alerta, motivo="Revisado")
        alerta.refresh_from_db()
        self.assertEqual(alerta.estado, EstadoAlerta.RESUELTA)

    def test_resuelta_no_cambia_y_el_detalle_tampoco(self):
        alerta = self.alerta_de(self.beto)
        with self.como_app(usuario_id=self.g1.pk):
            cambio = self.ajeno("UPDATE cuentas_alerta SET detalle = '{\"n\": 1}' WHERE id = %s", [alerta.pk])
            self.assertIsInstance(cambio, DatabaseError)
            services.resolver_alerta(self.g1, alerta, motivo="Revisado")
        with self.como_app(usuario_id=self.g2.pk):
            self.assertEqual(self.ajeno("UPDATE cuentas_alerta SET estado = 'ABIERTA' WHERE id = %s", [alerta.pk]), 0)
        with connection.cursor() as c:
            c.execute("SELECT estado FROM cuentas_alerta WHERE id = %s", [alerta.pk])
            self.assertEqual(c.fetchone()[0], "RESUELTA")
