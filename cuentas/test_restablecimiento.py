"""
Paso 11 · C17: restablecimiento del segundo factor con doble control.
AC solicita (AC·O7d SO); un GC distinto, tras verificar la identidad fuera del
sistema, autoriza (GC·O7d AU) o rechaza (RE). Al autorizar se invalidan los
factores de la persona y se cierran sus sesiones.
"""

import base64
import secrets
from datetime import timedelta

from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import TOTP
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from cuentas import rls, services, sesion
from cuentas.models import EstadoRestablecimiento, MetodoVerificacion, RestablecimientoFactor
from cuentas.selectores import metadatos_factores
from cuentas.test_gestion import EVENTOS, PERMISIVO, GestionBase, ultimo
from cuentas.test_rls import RLSBase
from cuentas.test_roles_c7 import abiertas, con_rol
from cuentas.tests import CLAVE
from nucleo.errores import (
    AccesoDenegado,
    DatoInvalido,
    FueraDeAlcance,
    SeparacionDeFunciones,
    TransicionInvalida,
)

PRESENCIAL = MetodoVerificacion.PRESENCIAL


def con_factores(usuario):
    secreto = base64.b32encode(secrets.token_bytes(20)).decode()
    TOTP.activate(usuario, secreto)
    RecoveryCodes.activate(usuario)
    return secreto


def factores(usuario) -> int:
    return Authenticator.objects.filter(user_id=usuario.pk).count()


class C17Base(GestionBase):
    def setUp(self):
        super().setUp()
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")
        self.tc = con_rol("tecnica", "TC")
        self.secreto = con_factores(self.tc)

    def solicitar(self, cuenta=None, por=None):
        return services.solicitar_restablecimiento(por or self.ac, cuenta or self.tc, motivo="Perdió el teléfono")

    def autorizar(self, r, por=None, metodo=PRESENCIAL, verificado_en=None):
        return services.autorizar_restablecimiento(
            por or self.g1, r, metodo_verificacion=metodo,
            verificado_en=verificado_en or timezone.now() - timedelta(hours=1))


class SolicitarTests(C17Base):
    def test_solicita_sin_cambiar_los_factores(self):
        r = self.solicitar()
        self.assertEqual((r.estado, r.solicitado_por, r.usuario), (EstadoRestablecimiento.SOLICITADO, self.ac, self.tc))
        self.assertEqual(factores(self.tc), 2)
        self.assertEqual(ultimo("factor:solicitar_restablecimiento")["despues"]["estado"], "SOLICITADO")

    def test_no_para_si_mismo(self):
        with self.assertRaises(SeparacionDeFunciones):
            self.solicitar(cuenta=self.ac)

    def test_cuenta_activa(self):
        services.dar_de_baja(self.ac, self.tc)
        with self.assertRaises(TransicionInvalida):
            self.solicitar()

    def test_motivo_requerido(self):
        for motivo in ("", "   ", None, "x" * 501):
            with self.subTest(motivo=motivo), self.assertRaises(DatoInvalido):
                services.solicitar_restablecimiento(self.ac, self.tc, motivo=motivo)

    def test_uno_en_curso_por_cuenta(self):
        self.solicitar()
        with self.assertRaises(TransicionInvalida):
            self.solicitar()

    @override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=False)
    def test_sin_motor_se_niega(self):
        with self.assertRaises(AccesoDenegado):
            self.solicitar()


class AutorizarTests(C17Base):
    def test_invalida_los_factores_y_cierra_sesiones(self):
        sesion.registrar_inicio(self.tc, "clave-tc")
        verificado = timezone.now() - timedelta(hours=2)
        r = self.autorizar(self.solicitar(), verificado_en=verificado)
        r.refresh_from_db()
        self.assertEqual((r.estado, r.resuelto_por, r.metodo_verificacion, r.verificado_en),
                         (EstadoRestablecimiento.AUTORIZADO, self.g1, PRESENCIAL, verificado))
        self.assertEqual(factores(self.tc), 0)
        self.assertEqual(abiertas(self.tc), 0)
        self.assertEqual(sorted(f["tipo"] for f in r.factores_invalidados), ["recovery_codes", "totp"])
        self.assertNotIn(self.secreto, str(r.factores_invalidados))
        self.assertNotIn("secret", str(r.factores_invalidados))
        evento = ultimo("factor:autorizar_restablecimiento")
        self.assertEqual((evento["despues"]["metodo"], evento["despues"]["sesiones_cerradas"]), ("PRESENCIAL", 1))

    def test_al_volver_a_entrar_debe_registrar_un_factor_nuevo(self):
        self.autorizar(self.solicitar())
        self.client.post(reverse("account_login"), {"login": "tecnica", "password": CLAVE})
        self.assertRedirects(self.client.get("/"), reverse("mfa_activate_totp"), fetch_redirect_response=False)

    def test_no_autoriza_quien_lo_pidio_ni_la_persona(self):
        con_factores(self.g2)
        r = self.solicitar(cuenta=self.g2, por=self.g1)  # con el motor permisivo, un GC podría pedirlo
        for gc in (self.g1, self.g2):
            with self.subTest(gc=gc.username), self.assertRaises(SeparacionDeFunciones):
                self.autorizar(r, por=gc)
        self.assertEqual(factores(self.g2), 2)
        r.refresh_from_db()
        self.assertEqual(r.estado, EstadoRestablecimiento.SOLICITADO)

    def test_exige_verificacion_registrada(self):
        r = self.solicitar()
        ahora = timezone.now()
        casos = [
            {"metodo": "CORREO"},
            {"metodo": ""},
            {"verificado_en": ahora.replace(tzinfo=None)},          # sin zona horaria
            {"verificado_en": ahora + timedelta(hours=1)},          # en el futuro
            {"verificado_en": ahora - timedelta(days=8)},           # demasiado vieja
            {"verificado_en": ahora.date()},                        # solo fecha
        ]
        for caso in casos:
            with self.subTest(caso=caso), self.assertRaises(DatoInvalido):
                datos = {"metodo": PRESENCIAL, "verificado_en": ahora - timedelta(hours=1), **caso}
                services.autorizar_restablecimiento(
                    self.g1, r, metodo_verificacion=datos["metodo"], verificado_en=datos["verificado_en"])
        self.assertEqual(factores(self.tc), 2)

    def test_se_resuelve_una_vez(self):
        r = self.autorizar(self.solicitar())
        with self.assertRaises(TransicionInvalida):
            self.autorizar(r, por=self.g2)
        with self.assertRaises(TransicionInvalida):
            services.rechazar_restablecimiento(self.g2, r, motivo="tarde")

    def test_cuenta_dada_de_baja_mientras_tanto(self):
        r = self.solicitar()
        services.dar_de_baja(self.ac, self.tc)
        with self.assertRaises(TransicionInvalida):
            self.autorizar(r)
        self.assertEqual(factores(self.tc), 2)


class RechazarTests(C17Base):
    def test_rechazar_no_toca_los_factores(self):
        r = services.rechazar_restablecimiento(self.g1, self.solicitar(), motivo=" No se pudo verificar ")
        r.refresh_from_db()
        self.assertEqual((r.estado, r.motivo_rechazo, r.resuelto_por),
                         (EstadoRestablecimiento.RECHAZADO, "No se pudo verificar", self.g1))
        self.assertEqual(factores(self.tc), 2)
        self.solicitar()  # se puede volver a pedir

    def test_motivo_requerido(self):
        with self.assertRaises(DatoInvalido):
            services.rechazar_restablecimiento(self.g1, self.solicitar(), motivo="")


class MetadatosTests(C17Base):
    def test_solo_tipo_y_fechas(self):
        datos = metadatos_factores(self.tc)
        self.assertEqual(sorted(d["tipo"] for d in datos), ["recovery_codes", "totp"])
        for d in datos:
            self.assertEqual(set(d), {"tipo", "creado_en", "usado_en"})
        self.assertEqual(metadatos_factores(self.ac), [])


class RestriccionesDeLaBaseTests(C17Base):
    def crear(self, **datos):
        base = {"usuario": self.tc, "motivo": "x", "solicitado_por": self.ac}
        with transaction.atomic():
            return RestablecimientoFactor.objects.create(**{**base, **datos})

    def test_autorizado_exige_metodo_y_fecha(self):
        resuelto = {"estado": EstadoRestablecimiento.AUTORIZADO, "resuelto_por": self.g1, "resuelto_en": timezone.now()}
        for extra in ({}, {"metodo_verificacion": PRESENCIAL}, {"verificado_en": timezone.now()}):
            with self.subTest(extra=extra), self.assertRaises(IntegrityError):
                self.crear(**resuelto, **extra)

    def test_tres_personas(self):
        for datos in ({"solicitado_por": self.tc},
                      {"estado": EstadoRestablecimiento.RECHAZADO, "resuelto_por": self.ac,
                       "resuelto_en": timezone.now()}):
            with self.subTest(datos=list(datos)), self.assertRaises(IntegrityError):
                self.crear(**datos)


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------
@PERMISIVO
class C17RLSTests(RLSBase):
    def setUp(self):
        super().setUp()
        EVENTOS.clear()
        self.ac = con_rol("admin_ac", "AC")
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")
        con_factores(self.beto)  # beto es VF

    def solicitar(self):
        with self.como_app(usuario_id=self.ac.pk):
            return services.solicitar_restablecimiento(self.ac, self.beto, motivo="Perdió el teléfono")

    def borrar_factores(self, usuario):
        return self.ajeno("DELETE FROM mfa_authenticator WHERE user_id = %s", [usuario.pk])

    def test_flujo_completo_con_rls(self):
        r = self.solicitar()
        with self.como_app(usuario_id=self.g1.pk):
            services.autorizar_restablecimiento(
                self.g1, r, metodo_verificacion=PRESENCIAL, verificado_en=timezone.now())
        r.refresh_from_db()
        self.assertEqual(factores(self.beto), 0)
        self.assertEqual(len(r.factores_invalidados), 2)  # el GC vio los metadatos antes de invalidar

    def test_quien_ve_los_metadatos(self):
        with self.como_app(usuario_id=self.ac.pk):
            self.assertEqual(len(metadatos_factores(self.beto)), 2)
        with self.como_app(usuario_id=self.ana.pk):  # PF
            self.assertEqual(metadatos_factores(self.beto), [])
        with self.como_app(usuario_id=self.g1.pk):  # GC sin restablecimiento en curso
            self.assertEqual(metadatos_factores(self.beto), [])
        self.solicitar()
        with self.como_app(usuario_id=self.g1.pk):
            self.assertEqual(len(metadatos_factores(self.beto)), 2)

    def test_nadie_borra_factores_sin_autorizacion_propia_y_reciente(self):
        r = self.solicitar()
        for sujeto in (self.ac, self.g1, self.ana):
            with self.subTest(sujeto=sujeto.username), self.como_app(usuario_id=sujeto.pk):
                self.assertEqual(self.borrar_factores(self.beto), 0)
        with self.como_app(usuario_id=self.g1.pk):
            services.autorizar_restablecimiento(
                self.g1, r, metodo_verificacion=PRESENCIAL, verificado_en=timezone.now())
        con_factores(self.beto)  # registra factores nuevos después
        with self.como_app(usuario_id=self.g2.pk):  # autorizado por otro GC
            self.assertEqual(self.borrar_factores(self.beto), 0)
        rls.fijar_contexto(fase="sistema")
        with connection.cursor() as c:
            c.execute("UPDATE cuentas_restablecimientofactor SET resuelto_en = now() - interval '10 minutes'"
                      " WHERE id = %s", [r.pk])
        rls.fijar_contexto()
        with self.como_app(usuario_id=self.g1.pk):  # su autorización ya no es reciente
            self.assertEqual(self.borrar_factores(self.beto), 0)
        self.assertEqual(factores(self.beto), 2)

    def test_solo_ac_solicita_y_solo_otro_gc_resuelve(self):
        with self.como_app(usuario_id=self.g1.pk):
            with self.assertRaises(FueraDeAlcance):  # no puede bloquear la cuenta: 404
                services.solicitar_restablecimiento(self.g1, self.beto, motivo="x")
        r = self.solicitar()
        resolver = ("UPDATE cuentas_restablecimientofactor SET estado = 'RECHAZADO', resuelto_por_id = %s,"
                    " resuelto_en = now(), motivo_rechazo = 'x' WHERE id = %s")
        for sujeto in (self.ac, self.beto):
            with self.subTest(sujeto=sujeto.username), self.como_app(usuario_id=sujeto.pk):
                self.assertEqual(self.ajeno(resolver, [sujeto.pk, r.pk]), 0)

    def test_resuelto_no_cambia(self):
        r = self.solicitar()
        with self.como_app(usuario_id=self.g1.pk):
            services.rechazar_restablecimiento(self.g1, r, motivo="no verificado")
        with self.como_app(usuario_id=self.g2.pk):
            self.assertEqual(self.ajeno(
                "UPDATE cuentas_restablecimientofactor SET estado = 'AUTORIZADO' WHERE id = %s", [r.pk]), 0)
