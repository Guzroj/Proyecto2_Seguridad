"""
Ventanas del auditor externo: C14 (GC·O7a AU), C5 (atributo que usará
el motor) y la tarea diaria que activa y da de baja cuentas AE. Incluye la
parte de cuentas de V9 (un día después del fin, el AE ya no tiene acceso).
"""

from datetime import datetime, time, timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cuentas import services, sesion
from cuentas.models import EstadoCuenta, EstadoVentana, VentanaAuditoria
from cuentas.selectores import roles_vigentes, ventana_vigente
from cuentas.test_gestion import EVENTOS, PERMISIVO, ultimo
from cuentas.test_rls import RLSBase
from cuentas.test_roles_c7 import abiertas, con_rol
from cuentas.tests import CLAVE, crear_cuenta
from nucleo.errores import DatoInvalido, SeparacionDeFunciones, TransicionInvalida

HOY = timezone.localdate()


def dia(n: int):
    return HOY + timedelta(days=n)


def momento(fecha, hora=time(12, 0)):
    """Un instante de ese día en hora de Costa Rica."""
    return timezone.make_aware(datetime.combine(fecha, hora))


@PERMISIVO
class VentanaBase(TestCase):
    def setUp(self):
        EVENTOS.clear()
        self.ac = con_rol("admin_ac", "AC")
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")

    def auditor(self, inicio=None, fin=None, username="auditora"):
        inicio = HOY if inicio is None else inicio
        fin = inicio + timedelta(days=10) if fin is None else fin
        return services.crear_cuenta(self.ac, username=username, rol="AE", inicio=inicio, fin=fin)

    def aprobada(self, **kwargs):
        cuenta = self.auditor(**kwargs)
        services.aprobar_ventana(self.g1, cuenta.ventana_auditoria)
        cuenta.refresh_from_db()
        return cuenta

    def tarea(self, fecha):
        with mock.patch("cuentas.selectores.hoy", return_value=fecha):
            return services.procesar_ventanas()


class CrearCuentaAETests(VentanaBase):
    def test_queda_pendiente_con_ventana_pendiente_y_sin_rol(self):
        cuenta = self.auditor(inicio=dia(2), fin=dia(5))
        v = cuenta.ventana_auditoria
        self.assertEqual((v.inicio, v.fin, v.estado, v.solicitada_por), (dia(2), dia(5), EstadoVentana.PENDIENTE, self.ac))
        self.assertEqual(cuenta.estado, EstadoCuenta.PENDIENTE)
        self.assertEqual(roles_vigentes(cuenta), set())

    def test_fechas_invalidas(self):
        casos = [
            {},                                       # sin ventana
            {"inicio": HOY},
            {"inicio": dia(5), "fin": dia(4)},        # termina antes de empezar
            {"inicio": HOY, "fin": dia(31)},          # más de 30 días
            {"inicio": dia(-10), "fin": dia(-1)},     # ya terminó
            {"inicio": timezone.now(), "fin": dia(2)},  # instante, no fecha
            {"inicio": "2026-01-01", "fin": "2026-01-02"},
        ]
        for caso in casos:
            with self.subTest(caso=caso), self.assertRaises(DatoInvalido):
                services.crear_cuenta(self.ac, username="auditora", rol="AE", **caso)
        self.assertFalse(VentanaAuditoria.objects.exists())

    def test_treinta_dias_exactos_se_aceptan(self):
        self.assertEqual(self.auditor(inicio=HOY, fin=dia(30)).ventana_auditoria.fin, dia(30))

    def test_solo_ae_lleva_ventana(self):
        with self.assertRaises(DatoInvalido):
            services.crear_cuenta(self.ac, username="tecnica", rol="TC", inicio=HOY, fin=dia(1))


class AprobarVentanaTests(VentanaBase):
    def test_ventana_que_empieza_hoy_activa_la_cuenta(self):
        cuenta = self.aprobada(inicio=HOY, fin=dia(3))
        v = cuenta.ventana_auditoria
        self.assertEqual((v.estado, v.aprobada_por), (EstadoVentana.APROBADA, self.g1))
        self.assertEqual(cuenta.estado, EstadoCuenta.ACTIVA)
        self.assertEqual(roles_vigentes(cuenta), {"AE"})
        self.assertEqual(ventana_vigente(cuenta), v)
        self.assertEqual(ultimo("ventana:autorizar")["despues"]["estado_cuenta"], "ACTIVA")

    def test_el_rol_ae_dura_exactamente_la_ventana(self):
        cuenta = self.aprobada(inicio=HOY, fin=dia(3))
        self.assertEqual(roles_vigentes(cuenta, en=momento(dia(3), time(23, 59))), {"AE"})
        self.assertEqual(roles_vigentes(cuenta, en=momento(dia(4), time(0, 0))), set())

    def test_ventana_futura_queda_pendiente_hasta_el_inicio(self):
        cuenta = self.aprobada(inicio=dia(3), fin=dia(6))
        self.assertEqual(cuenta.estado, EstadoCuenta.PENDIENTE)
        self.assertEqual(roles_vigentes(cuenta), set())
        self.assertEqual(roles_vigentes(cuenta, en=momento(dia(3), time(0, 0))), {"AE"})

    def test_no_aprueba_quien_la_pidio_ni_el_auditor(self):
        # Con el motor permisivo, un GC podría pedirla; aun así no la aprueba él.
        cuenta = services.crear_cuenta(self.g1, username="auditora", rol="AE", inicio=HOY, fin=dia(1))
        with self.assertRaises(SeparacionDeFunciones):
            services.aprobar_ventana(self.g1, cuenta.ventana_auditoria)
        with self.assertRaises(SeparacionDeFunciones):
            services.aprobar_ventana(cuenta, cuenta.ventana_auditoria)
        self.assertEqual(VentanaAuditoria.objects.get().estado, EstadoVentana.PENDIENTE)
        self.assertEqual(ultimo("ventana:autorizar")["resultado"], "DENEGADO")

    def test_se_aprueba_una_vez(self):
        cuenta = self.aprobada()
        with self.assertRaises(TransicionInvalida):
            services.aprobar_ventana(self.g2, cuenta.ventana_auditoria)

    def test_ventana_vencida_no_se_aprueba(self):
        cuenta = self.auditor(inicio=HOY, fin=dia(2))
        with mock.patch("cuentas.selectores.hoy", return_value=dia(3)), self.assertRaises(TransicionInvalida):
            services.aprobar_ventana(self.g1, cuenta.ventana_auditoria)

    def test_aprobada_tarde_el_rol_empieza_al_aprobar(self):
        cuenta = self.auditor(inicio=HOY, fin=dia(5))
        services.aprobar_ventana(self.g1, cuenta.ventana_auditoria)
        pertenencia = cuenta.roles.get()
        v = VentanaAuditoria.objects.get(pk=cuenta.ventana_auditoria.pk)
        self.assertGreaterEqual(pertenencia.vigente_desde, v.aprobada_en)

    def test_cuenta_dada_de_baja_mientras_tanto(self):
        cuenta = self.auditor()
        services.dar_de_baja(self.ac, cuenta)
        with self.assertRaises(TransicionInvalida):
            services.aprobar_ventana(self.g1, cuenta.ventana_auditoria)


class C5Tests(VentanaBase):
    def test_solo_dentro_de_la_ventana(self):
        cuenta = self.aprobada(inicio=HOY, fin=dia(2))
        self.assertIsNotNone(ventana_vigente(cuenta, en=momento(dia(2), time(23, 59))))
        self.assertIsNone(ventana_vigente(cuenta, en=momento(dia(3), time(0, 1))))  # V9: un día después
        self.assertIsNone(ventana_vigente(cuenta, en=momento(dia(-1))))

    def test_sin_aprobar_no_habilita(self):
        cuenta = self.auditor()
        self.assertIsNone(ventana_vigente(cuenta))

    def test_cuenta_no_activa_no_habilita(self):
        cuenta = self.aprobada()
        services.dar_de_baja(self.ac, cuenta)
        self.assertIsNone(ventana_vigente(cuenta))

    def test_quien_aprobo_debia_ser_gc_al_aprobar(self):
        cuenta = self.auditor()
        persona = con_rol("sin_gc", "PF")
        with mock.patch("nucleo.ganchos.autorizar"):
            services.aprobar_ventana(persona, cuenta.ventana_auditoria)
        self.assertIsNone(ventana_vigente(cuenta))

    def test_si_el_gc_deja_de_serlo_despues_la_ventana_sigue(self):
        cuenta = self.aprobada()
        services.terminar_rol(self.g1, "GC")
        self.assertIsNotNone(ventana_vigente(cuenta))

    def test_otros_roles_no_tienen_ventana(self):
        self.assertIsNone(ventana_vigente(self.g1))


class TareaDiariaTests(VentanaBase):
    def test_activa_la_cuenta_el_dia_de_inicio(self):
        cuenta = self.aprobada(inicio=dia(2), fin=dia(4))
        self.assertEqual(self.tarea(dia(1)), {"activadas": 0, "dadas_de_baja": 0})
        self.assertEqual(self.tarea(dia(2)), {"activadas": 1, "dadas_de_baja": 0})
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.ACTIVA)
        self.assertIsNone(ultimo("cuenta:activar")["actor"])
        self.assertEqual(self.tarea(dia(2)), {"activadas": 0, "dadas_de_baja": 0})  # idempotente

    def test_ventana_pendiente_no_activa(self):
        cuenta = self.auditor(inicio=HOY, fin=dia(2))
        self.tarea(dia(1))
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.PENDIENTE)

    def test_da_de_baja_las_vencidas_y_cierra_sesiones(self):
        cuenta = self.aprobada(inicio=HOY, fin=dia(2))
        sesion.registrar_inicio(cuenta, "clave-ae")
        self.assertEqual(self.tarea(dia(2))["dadas_de_baja"], 0)
        self.assertEqual(self.tarea(dia(3)), {"activadas": 0, "dadas_de_baja": 1})
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.INACTIVA)
        self.assertEqual(abiertas(cuenta), 0)
        self.assertEqual(ultimo("cuenta:baja")["despues"]["sesiones_cerradas"], 1)
        self.assertEqual(self.tarea(dia(3))["dadas_de_baja"], 0)  # idempotente

    def test_da_de_baja_las_que_nunca_se_aprobaron(self):
        cuenta = self.auditor(inicio=HOY, fin=dia(1))
        self.tarea(dia(2))
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.INACTIVA)

    def test_no_toca_otras_cuentas(self):
        otra = con_rol("rosa", "PF")
        self.aprobada(inicio=HOY, fin=dia(1))
        self.tarea(dia(40))
        otra.refresh_from_db()
        self.assertEqual(otra.estado, EstadoCuenta.ACTIVA)
        for cuenta in (self.ac, self.g1):
            cuenta.refresh_from_db()
            self.assertEqual(cuenta.estado, EstadoCuenta.ACTIVA)

    def test_comando(self):
        cuenta = self.aprobada(inicio=HOY, fin=dia(1))
        salida = StringIO()
        with mock.patch("cuentas.selectores.hoy", return_value=dia(2)):
            call_command("tarea_diaria_cuentas", stdout=salida)
        self.assertIn("Dadas de baja: 1", salida.getvalue())
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.INACTIVA)


class AuditorIngresaTests(VentanaBase):
    def test_entra_durante_la_ventana_y_no_despues(self):
        cuenta = self.aprobada(inicio=HOY, fin=dia(1))
        cuenta.set_password(CLAVE)
        cuenta.save(update_fields=["password"])
        self.client.post(reverse("account_login"), {"login": "auditora", "password": CLAVE})
        self.assertContains(self.client.get(reverse("inicio")), "AE")
        self.tarea(dia(2))
        self.assertEqual(self.client.get(reverse("inicio")).status_code, 302)


class RestriccionesDeLaBaseTests(VentanaBase):
    def crear(self, **datos):
        base = {"auditor": crear_cuenta("aud"), "inicio": HOY, "fin": dia(1), "solicitada_por": self.ac}
        with transaction.atomic():
            return VentanaAuditoria.objects.create(**{**base, **datos})

    def test_maximo_30_dias(self):
        with self.assertRaises(IntegrityError):
            self.crear(fin=dia(31))
        with self.assertRaises(IntegrityError):
            self.crear(fin=dia(-1))

    def test_tres_personas_distintas(self):
        aud = crear_cuenta("aud2")
        casos = [
            {"auditor": aud, "solicitada_por": aud},
            {"estado": EstadoVentana.APROBADA, "aprobada_por": self.ac, "aprobada_en": timezone.now()},
        ]
        for caso in casos:
            with self.subTest(caso=caso), self.assertRaises(IntegrityError):
                self.crear(**caso)

    def test_aprobacion_coherente(self):
        with self.assertRaises(IntegrityError):
            self.crear(estado=EstadoVentana.APROBADA)

    def test_una_ventana_por_cuenta(self):
        aud = crear_cuenta("aud3")
        self.crear(auditor=aud)
        with self.assertRaises(IntegrityError):
            self.crear(auditor=aud)


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------
@PERMISIVO
class VentanasRLSTests(RLSBase):
    def setUp(self):
        super().setUp()
        EVENTOS.clear()
        self.ac = con_rol("admin_ac", "AC")
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")

    def crear_auditor(self, username="auditora"):
        with self.como_app(usuario_id=self.ac.pk):
            return services.crear_cuenta(self.ac, username=username, rol="AE", inicio=HOY, fin=dia(3))

    def test_flujo_completo_con_rls(self):
        cuenta = self.crear_auditor()
        with self.como_app(usuario_id=self.g1.pk):
            services.aprobar_ventana(self.g1, cuenta.ventana_auditoria)
        cuenta.refresh_from_db()
        self.assertEqual((cuenta.estado, roles_vigentes(cuenta)), (EstadoCuenta.ACTIVA, {"AE"}))
        with self.como_app(fase="sistema"), mock.patch("cuentas.selectores.hoy", return_value=dia(4)):
            services.procesar_ventanas()
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.INACTIVA)

    def test_gc_no_da_ae_sin_ventana_aprobada_por_el(self):
        cuenta = self.crear_auditor()
        insertar = ("INSERT INTO cuentas_rolusuario (usuario_id, rol, vigente_desde, creado_en)"
                    " VALUES (%s, 'AE', now(), now())")
        with self.como_app(usuario_id=self.g1.pk):
            self.assertIsInstance(self.ajeno(insertar, [cuenta.pk]), DatabaseError)  # ventana pendiente
            services.aprobar_ventana(self.g1, cuenta.ventana_auditoria)
        with self.como_app(usuario_id=self.g2.pk):  # aprobada por otro GC
            self.assertIsInstance(self.ajeno(insertar, [self.ana.pk]), DatabaseError)

    def test_ventana_aprobada_no_cambia(self):
        cuenta = self.crear_auditor()
        v = cuenta.ventana_auditoria
        with self.como_app(usuario_id=self.g1.pk):
            cambio = self.ajeno("UPDATE cuentas_ventanaauditoria SET fin = fin + 20 WHERE id = %s", [v.pk])
            self.assertIsInstance(cambio, DatabaseError)  # pendiente: el disparador protege las fechas
            services.aprobar_ventana(self.g1, v)
        with self.como_app(usuario_id=self.g2.pk):
            self.assertEqual(
                self.ajeno("UPDATE cuentas_ventanaauditoria SET estado = 'PENDIENTE' WHERE id = %s", [v.pk]), 0)

    def test_ac_no_aprueba_y_otros_roles_no_ven_ventanas(self):
        cuenta = self.crear_auditor()
        with self.como_app(usuario_id=self.ac.pk):
            filas = self.ajeno(
                "UPDATE cuentas_ventanaauditoria SET estado = 'APROBADA', aprobada_por_id = %s, aprobada_en = now()"
                " WHERE id = %s", [self.ac.pk, cuenta.ventana_auditoria.pk])
            self.assertEqual(filas, 0)
        with self.como_app(usuario_id=self.beto.pk):  # VF
            self.assertEqual(VentanaAuditoria.objects.count(), 0)
        with self.como_app(usuario_id=self.g1.pk):
            self.assertEqual(VentanaAuditoria.objects.count(), 1)

    def test_solo_ac_crea_ventanas(self):
        with self.como_app(usuario_id=self.g1.pk):
            with self.assertRaises(DatabaseError), transaction.atomic():
                services.crear_cuenta(self.g1, username="auditora", rol="AE", inicio=HOY, fin=dia(1))
