"""Paso 6 · Segundo factor (TOTP + códigos de recuperación), reautenticación de 300 s y usuarios de prueba."""

import base64
import os
import secrets
import time
from datetime import timedelta
from io import StringIO
from unittest import mock

from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, hotp_value
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from cuentas import adaptador, mfa, services, sesion
from cuentas.models import EstadoCuenta, RolUsuario, Sesion, Usuario
from cuentas.test_rls import RLSBase
from cuentas.tests import CLAVE, crear_cuenta
from nucleo.errores import ReautenticacionRequerida

SECRETO = base64.b32encode(secrets.token_bytes(20)).decode()


def codigo_actual(secreto=SECRETO) -> str:
    return format_hotp_value(hotp_value(secreto, int(time.time()) // 30))


class MFABase(TestCase):
    def setUp(self):
        cache.clear()  # allauth marca códigos TOTP usados en la caché

    def crear(self, username, *roles_usuario, totp=False):
        u = crear_cuenta(username, EstadoCuenta.ACTIVA)
        for r in roles_usuario:
            services.agregar_rol(u, r)
        if totp:
            TOTP.activate(u, SECRETO)
            RecoveryCodes.activate(u)
        return u

    def entrar_con_clave(self, username):
        return self.client.post(reverse("account_login"), {"login": username, "password": CLAVE})

    def entrar_completo(self, username):
        self.entrar_con_clave(username)
        return self.client.post(reverse("mfa_authenticate"), {"code": codigo_actual()})


class CifradoTests(MFABase):
    def test_secreto_totp_cifrado_en_la_base(self):
        u = self.crear("ana", "TC", totp=True)
        guardado = Authenticator.objects.get(user=u, type=Authenticator.Type.TOTP).data["secret"]
        self.assertNotIn(SECRETO, guardado)
        self.assertEqual(adaptador.AdaptadorMFA().decrypt(guardado), SECRETO)

    def test_semilla_de_recuperacion_cifrada(self):
        u = self.crear("ana", "TC", totp=True)
        datos = Authenticator.objects.get(user=u, type=Authenticator.Type.RECOVERY_CODES).data
        semilla = adaptador.AdaptadorMFA().decrypt(datos["seed"])
        self.assertNotEqual(datos["seed"], semilla)

    def test_clave_propia_cambia_el_cifrado(self):
        texto = adaptador.AdaptadorMFA().encrypt("secreto")
        with override_settings(MFA_CLAVE_CIFRADO="otra-clave-de-cifrado-larga"):
            self.assertNotEqual(adaptador.clave_cifrado_mfa(), base64.urlsafe_b64encode(b"x" * 32))
            with self.assertRaises(Exception):
                adaptador.AdaptadorMFA().decrypt(texto)

    def test_roles_con_mfa_no_pueden_quitarse_el_factor(self):
        for rol, puede in (("TC", False), ("VF", False), ("GC", False), ("AC", False), ("AE", True), ("PF", True)):
            with self.subTest(rol=rol):
                u = self.crear(f"u-{rol}", rol, totp=True)
                totp = Authenticator.objects.get(user=u, type=Authenticator.Type.TOTP)
                self.assertEqual(adaptador.AdaptadorMFA().can_delete_authenticator(totp), puede)


class ExigirMFATests(MFABase):
    def reautenticar_con_clave(self):
        return self.client.post(reverse("account_reauthenticate"), {"password": CLAVE})

    def test_roles_con_mfa_sin_totp_solo_pueden_activarlo(self):
        for rol in ("TC", "VF", "GC", "AC"):
            with self.subTest(rol=rol):
                u = self.crear(f"sin-{rol}", rol)
                self.entrar_con_clave(u.username)
                self.assertRedirects(self.client.get("/"), reverse("mfa_activate_totp"), fetch_redirect_response=False)
                # allauth pide la contraseña otra vez antes de registrar un factor; sin ciclos.
                self.assertEqual(self.client.get(reverse("account_reauthenticate")).status_code, 200)
                self.reautenticar_con_clave()
                self.assertEqual(self.client.get(reverse("mfa_activate_totp")).status_code, 200)
                self.client.logout()

    def test_pf_y_ae_no_estan_obligados(self):
        for rol in ("PF", "AE"):
            with self.subTest(rol=rol):
                self.client.force_login(self.crear(f"sin-{rol}", rol))
                self.assertEqual(self.client.get("/").status_code, 200)
                self.client.logout()

    def test_puede_salir_sin_activar(self):
        self.client.force_login(self.crear("ana", "TC"))
        self.client.post(reverse("account_logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_activar_totp_desde_la_pantalla(self):
        u = self.crear("ana", "TC")
        self.entrar_con_clave("ana")
        self.reautenticar_con_clave()
        self.client.get(reverse("mfa_activate_totp"))
        secreto = self.client.session["mfa.totp.secret"]
        self.client.post(reverse("mfa_activate_totp"), {"code": codigo_actual(secreto)})
        self.assertTrue(mfa.tiene_totp(u))
        self.assertEqual(self.client.get("/").status_code, 200)


class ActivacionTOTPTests(TransactionTestCase):
    """
    allauth avisa de la activación con on_commit: hace falta confirmar de verdad
    la transacción (en TestCase el aviso llegaría después de guardar la sesión).
    """

    def setUp(self):
        cache.clear()

    def test_activar_cuenta_como_mfa_reciente_y_renueva_el_identificador(self):
        u = crear_cuenta("ana", EstadoCuenta.ACTIVA)
        services.agregar_rol(u, "TC")
        self.client.post(reverse("account_login"), {"login": "ana", "password": CLAVE})
        self.client.post(reverse("account_reauthenticate"), {"password": CLAVE})
        self.client.get(reverse("mfa_activate_totp"))
        clave_antes = self.client.session.session_key
        secreto = self.client.session["mfa.totp.secret"]
        respuesta = self.client.post(reverse("mfa_activate_totp"), {"code": codigo_actual(secreto)})
        # Va directo a los códigos de recuperación, sin pedir un segundo código.
        self.assertRedirects(respuesta, reverse("mfa_view_recovery_codes"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(respuesta.url).status_code, 200)
        self.assertNotEqual(self.client.session.session_key, clave_antes)
        registro = Sesion.objects.get(usuario=u, cerrada_en__isnull=True)
        self.assertEqual(registro.clave_hash, sesion.hash_clave(self.client.session.session_key))


class C21Tests(MFABase):
    def test_con_factor_registrado_ver_o_generar_codigos_exige_mfa_reciente(self):
        self.crear("ana", "TC", totp=True)
        self.entrar_completo("ana")
        sesion_ = self.client.session
        registros = sesion_[AUTHENTICATION_METHODS_SESSION_KEY]
        for r in registros:
            if r["method"] == "mfa":
                r["at"] = time.time() - 400  # segundo factor de hace más de 300 s
        sesion_[AUTHENTICATION_METHODS_SESSION_KEY] = registros
        sesion_.save()
        self.client.post(reverse("account_reauthenticate"), {"password": CLAVE})  # la contraseña sola no basta
        for ruta in ("mfa_generate_recovery_codes", "mfa_view_recovery_codes"):
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(reverse(ruta))
                self.assertEqual(respuesta.status_code, 302)
                self.assertTrue(respuesta["Location"].startswith(reverse("mfa_reauthenticate")))
        cache.clear()
        self.client.post(reverse("mfa_reauthenticate"), {"code": codigo_actual()})
        self.assertEqual(self.client.get(reverse("mfa_view_recovery_codes")).status_code, 200)


class LoginConSegundoFactorTests(MFABase):
    def test_contrasena_sola_no_abre_sesion(self):
        self.crear("ana", "TC", totp=True)
        respuesta = self.entrar_con_clave("ana")
        self.assertRedirects(respuesta, reverse("mfa_authenticate"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotEqual(self.client.get("/").status_code, 200)

    def test_codigo_correcto_abre_sesion_y_registra_el_factor(self):
        u = self.crear("ana", "TC", totp=True)
        respuesta = self.entrar_completo("ana")
        self.assertRedirects(respuesta, reverse("inicio"), fetch_redirect_response=False)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertTrue(Sesion.objects.filter(usuario=u, cerrada_en__isnull=True).exists())

    def test_codigo_incorrecto(self):
        self.crear("ana", "TC", totp=True)
        self.entrar_con_clave("ana")
        malo = f"{(int(codigo_actual()) + 1) % 1_000_000:06d}"
        self.client.post(reverse("mfa_authenticate"), {"code": malo})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_codigo_de_recuperacion(self):
        u = self.crear("ana", "TC", totp=True)
        codigo = RecoveryCodes(Authenticator.objects.get(user=u, type=Authenticator.Type.RECOVERY_CODES)).get_unused_codes()[0]
        self.entrar_con_clave("ana")
        self.client.post(reverse("mfa_authenticate"), {"code": codigo})
        self.assertIn("_auth_user_id", self.client.session)


class EdadMFATests(MFABase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def peticion(self, registros):
        request = self.factory.get("/")
        request.session = {AUTHENTICATION_METHODS_SESSION_KEY: registros}
        return request

    def test_sin_segundo_factor(self):
        r = self.peticion([{"method": "password", "at": time.time()}])
        self.assertIsNone(mfa.edad_mfa(r))
        self.assertFalse(mfa.mfa_reciente(r))

    def test_limite_de_300_segundos(self):
        self.assertTrue(mfa.mfa_reciente(self.peticion([{"method": "mfa", "at": time.time() - 290}])))
        self.assertFalse(mfa.mfa_reciente(self.peticion([{"method": "mfa", "at": time.time() - 310}])))

    def test_toma_el_mas_reciente(self):
        r = self.peticion([{"method": "mfa", "at": time.time() - 900}, {"method": "mfa", "at": time.time() - 10}])
        self.assertLess(mfa.edad_mfa(r), 60)

    def test_login_completo_deja_mfa_reciente(self):
        self.crear("ana", "TC", totp=True)
        self.entrar_completo("ana")
        registros = self.client.session[AUTHENTICATION_METHODS_SESSION_KEY]
        self.assertTrue(any(r["method"] == "mfa" for r in registros))


class RetoRG6Tests(MFABase):
    def test_vista_protegida_lanza_reto(self):
        vista = mfa.requiere_mfa_reciente(lambda request: HttpResponse("ok"))
        request = RequestFactory().get("/aprobar/")
        request.session = {}
        with self.assertRaises(ReautenticacionRequerida):
            vista(request)
        request.session = {AUTHENTICATION_METHODS_SESSION_KEY: [{"method": "mfa", "at": time.time()}]}
        self.assertEqual(vista(request).content, b"ok")

    def test_middleware_convierte_el_reto_en_redireccion(self):
        request = RequestFactory().get("/aprobar/?x=1")
        request.user = self.crear("ana", "VF", totp=True)
        respuesta = mfa.ExigirMFAMiddleware(lambda r: None).process_exception(
            request, ReautenticacionRequerida("x")
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(respuesta["Location"].startswith(reverse("mfa_reauthenticate")))
        self.assertIn("next=%2Faprobar%2F%3Fx%3D1", respuesta["Location"])

    def test_otras_negaciones_no_se_convierten(self):
        from nucleo.errores import AccesoDenegado

        request = RequestFactory().get("/")
        request.user = self.crear("ana", "VF", totp=True)
        self.assertIsNone(mfa.ExigirMFAMiddleware(lambda r: None).process_exception(request, AccesoDenegado("x")))


class ReautenticacionTests(MFABase):
    def test_reautenticar_renueva_el_identificador_y_conserva_la_sesion(self):
        u = self.crear("ana", "VF", totp=True)
        self.entrar_completo("ana")
        clave_antes = self.client.session.session_key
        registro = Sesion.objects.get(usuario=u, cerrada_en__isnull=True)
        cache.clear()  # el mismo código no se puede usar dos veces
        self.client.post(reverse("mfa_reauthenticate"), {"code": codigo_actual()})
        clave_despues = self.client.session.session_key
        self.assertNotEqual(clave_antes, clave_despues)
        registro.refresh_from_db()
        self.assertEqual(registro.clave_hash, sesion.hash_clave(clave_despues))
        self.assertIsNone(registro.cerrada_en)
        self.assertEqual(self.client.get("/").status_code, 200)


class PreingresoRLSTests(RLSBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        TOTP.activate(self.ana, SECRETO)
        TOTP.activate(self.beto, SECRETO)

    def test_en_preingreso_solo_ve_lo_propio(self):
        with self.como_app(usuario_id=self.ana.pk, fase="preingreso"):
            self.assertEqual(list(Usuario.objects.values_list("pk", flat=True)), [self.ana.pk])
            self.assertEqual(RolUsuario.objects.count(), 0)
            self.assertEqual(list(Authenticator.objects.values_list("user_id", flat=True)), [self.ana.pk])

    def test_factores_ajenos_no_se_ven_ni_autenticado(self):
        with self.como_app(usuario_id=self.ana.pk):
            self.assertEqual(set(Authenticator.objects.values_list("user_id", flat=True)), {self.ana.pk})

    def test_login_con_segundo_factor_bajo_rls(self):
        with self.como_app():
            self.client.post(reverse("account_login"), {"login": "ana", "password": CLAVE})
            self.assertNotIn("_auth_user_id", self.client.session)
            respuesta = self.client.post(reverse("mfa_authenticate"), {"code": codigo_actual()})
            self.assertRedirects(respuesta, reverse("inicio"), fetch_redirect_response=False)
            self.assertEqual(self.client.get("/").status_code, 200)


class UsuariosPruebaTests(TestCase):
    ENTORNO = {"VZ_PRUEBA_CLAVE": "Clave-De-Prueba-2026!", "VZ_PRUEBA_TOTP": SECRETO}

    def correr(self, entorno=None):
        salida = StringIO()
        with mock.patch.dict(os.environ, entorno or self.ENTORNO, clear=False):
            call_command("crear_usuarios_prueba", stdout=salida)
        return salida.getvalue()

    def test_crea_una_cuenta_activa_por_rol(self):
        salida = self.correr()
        esperado = {"prueba_tc": "TC", "prueba_tc2": "TC", "prueba_vf": "VF", "prueba_vf2": "VF",
                    "prueba_vf3": "VF", "prueba_gc": "GC", "prueba_gc2": "GC", "prueba_ac": "AC",
                    "prueba_ae": "AE", "prueba_pf": "PF"}
        for username, rol in esperado.items():
            with self.subTest(username=username):
                u = Usuario.objects.get(username=username)
                self.assertEqual(u.estado, EstadoCuenta.ACTIVA)
                self.assertTrue(RolUsuario.objects.filter(usuario=u, rol=rol).exists())
                self.assertEqual(mfa.tiene_totp(u), rol != "PF")
        self.assertIn("otpauth://totp/", salida)
        self.assertNotIn(self.ENTORNO["VZ_PRUEBA_CLAVE"], salida)

    def test_la_cuenta_ae_tiene_ventana_aprobada_de_30_dias(self):
        from cuentas.selectores import hoy, ventana_vigente

        self.correr()
        ae = Usuario.objects.get(username="prueba_ae")
        v = ventana_vigente(ae)
        self.assertIsNotNone(v)
        self.assertEqual((v.inicio, (v.fin - v.inicio).days), (hoy(), 30))
        self.assertEqual((v.solicitada_por.username, v.aprobada_por.username), ("prueba_ac", "prueba_gc"))
        self.assertEqual(RolUsuario.objects.get(usuario=ae).vigente_hasta.date(), v.fin + timedelta(days=1))

    def test_las_cuentas_creadas_ingresan_con_el_secreto_conocido(self):
        self.correr()
        cache.clear()
        self.client.post(reverse("account_login"), {"login": "prueba_gc", "password": self.ENTORNO["VZ_PRUEBA_CLAVE"]})
        self.client.post(reverse("mfa_authenticate"), {"code": codigo_actual()})
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_es_idempotente(self):
        self.correr()
        salida = self.correr()
        self.assertIn("0 cuentas creadas, 10 ya existían", salida)

    def test_rechaza_secretos_debiles_o_ausentes(self):
        casos = [
            {"VZ_PRUEBA_CLAVE": "", "VZ_PRUEBA_TOTP": SECRETO},
            {"VZ_PRUEBA_CLAVE": "corta", "VZ_PRUEBA_TOTP": SECRETO},
            {"VZ_PRUEBA_CLAVE": self.ENTORNO["VZ_PRUEBA_CLAVE"], "VZ_PRUEBA_TOTP": "ABC"},
            {"VZ_PRUEBA_CLAVE": self.ENTORNO["VZ_PRUEBA_CLAVE"], "VZ_PRUEBA_TOTP": "1" * 40},
        ]
        for entorno in casos:
            with self.subTest(entorno=entorno), self.assertRaises(CommandError):
                self.correr(entorno)
        self.assertFalse(Usuario.objects.filter(username__startswith="prueba_").exists())
