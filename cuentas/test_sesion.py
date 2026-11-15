"""Paso 5 · Inactividad por rol, máximo absoluto de 10 h y cierre de sesiones."""

import base64
import secrets
from datetime import timedelta

from allauth.mfa.totp.internal.auth import TOTP
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from autorizacion import roles
from cuentas import services, sesion
from cuentas.models import EstadoCuenta, MotivoCierre, Sesion
from cuentas.test_rls import RLSBase
from cuentas.tests import CLAVE, crear_cuenta

INICIO = "/"


class LimiteInactividadTests(SimpleTestCase):
    def test_por_rol(self):
        casos = {
            ("GC",): 15, ("AC",): 15, ("AE",): 15,
            ("TC",): 30, ("VF",): 30, ("PF",): 30,
            ("TC", "PF"): 30, ("GC", "PF"): 15, ("VF", "PF"): 30,
            (): 15,
        }
        for roles_usuario, minutos in casos.items():
            with self.subTest(roles=roles_usuario):
                self.assertEqual(sesion.limite_inactividad(set(roles_usuario)), timedelta(minutes=minutos))


class SesionBase(TestCase):
    def crear(self, username, *roles_usuario):
        u = crear_cuenta(username, EstadoCuenta.ACTIVA)
        for r in roles_usuario:
            services.agregar_rol(u, r)
        if set(roles_usuario) & roles.ROLES_CON_MFA:
            # Sin TOTP, ExigirMFAMiddleware llevaría a activarlo (paso 6).
            TOTP.activate(u, base64.b32encode(secrets.token_bytes(20)).decode())
        return u

    def entrar(self, usuario, cliente=None):
        cliente = cliente or self.client
        self.assertTrue(cliente.login(username=usuario.username, password=CLAVE))
        return cliente

    def registro(self, cliente):
        return Sesion.objects.get(clave_hash=sesion.hash_clave(cliente.session.session_key))

    def envejecer(self, cliente, *, inactivo=None, desde_inicio=None):
        r = self.registro(cliente)
        ahora = timezone.now()
        if inactivo is not None:
            r.ultima_actividad = ahora - inactivo
        if desde_inicio is not None:
            r.iniciada_en = ahora - desde_inicio
        r.save(update_fields=["ultima_actividad", "iniciada_en"])

    def sigue_dentro(self, cliente) -> bool:
        return cliente.get(INICIO).status_code == 200


class RegistroTests(SesionBase):
    def test_login_registra_la_sesion_con_hash(self):
        u = self.crear("ana", "TC")
        self.entrar(u)
        r = self.registro(self.client)
        self.assertEqual(r.usuario, u)
        self.assertIsNone(r.cerrada_en)
        self.assertNotEqual(r.clave_hash, self.client.session.session_key)
        self.assertEqual(len(r.clave_hash), 64)

    def test_login_por_formulario_de_allauth_tambien_registra(self):
        u = self.crear("ana", "PF")
        self.client.post(reverse("account_login"), {"login": "ana", "password": CLAVE})
        self.assertEqual(Sesion.objects.filter(usuario=u, cerrada_en__isnull=True).count(), 1)

    def test_actividad_actualiza_la_ultima_actividad(self):
        u = self.crear("ana", "TC")
        self.entrar(u)
        self.envejecer(self.client, inactivo=timedelta(minutes=10))
        antes = self.registro(self.client).ultima_actividad
        self.assertTrue(self.sigue_dentro(self.client))
        self.assertGreater(self.registro(self.client).ultima_actividad, antes)

    def test_logout_cierra_el_registro(self):
        u = self.crear("ana", "TC")
        self.entrar(u)
        r = self.registro(self.client)
        self.client.post(reverse("account_logout"))
        r.refresh_from_db()
        self.assertEqual(r.motivo_cierre, MotivoCierre.SALIDA)
        self.assertIsNotNone(r.cerrada_en)


class InactividadTests(SesionBase):
    CASOS = [
        (("TC",), 29, True), (("TC",), 31, False),
        (("VF",), 29, True), (("VF",), 31, False),
        (("PF",), 29, True), (("PF",), 31, False),
        (("GC",), 14, True), (("GC",), 16, False),
        (("AC",), 14, True), (("AC",), 16, False),
        (("AE",), 14, True), (("AE",), 16, False),
        (("GC", "PF"), 16, False),
        ((), 16, False),
    ]

    def test_inactividad_por_rol(self):
        for i, (roles_usuario, minutos, dentro) in enumerate(self.CASOS):
            with self.subTest(roles=roles_usuario, minutos=minutos):
                u = self.crear(f"u{i}", *roles_usuario)
                cliente = self.entrar(u, Client())
                self.envejecer(cliente, inactivo=timedelta(minutes=minutos))
                registro = self.registro(cliente)
                self.assertEqual(self.sigue_dentro(cliente), dentro)
                if not dentro:
                    registro.refresh_from_db()
                    self.assertEqual(registro.motivo_cierre, MotivoCierre.INACTIVIDAD)
                    self.assertNotIn("_auth_user_id", cliente.session)

    def test_roles_se_leen_en_cada_peticion_y_no_de_la_sesion(self):
        u = self.crear("ana", "TC")
        self.entrar(u)
        self.assertTrue(self.sigue_dentro(self.client))
        services.agregar_rol(u, "GC")  # ahora el límite baja a 15 min
        self.envejecer(self.client, inactivo=timedelta(minutes=20))
        self.assertFalse(self.sigue_dentro(self.client))

    def test_expulsado_es_redirigido_al_login_con_aviso(self):
        u = self.crear("ana", "GC")
        self.entrar(u)
        self.envejecer(self.client, inactivo=timedelta(minutes=16))
        respuesta = self.client.get(INICIO, follow=True)
        self.assertContains(respuesta, "inactividad")


class MaximoAbsolutoTests(SesionBase):
    def test_diez_horas_aunque_haya_actividad(self):
        u = self.crear("ana", "TC")
        self.entrar(u)
        self.envejecer(self.client, inactivo=timedelta(minutes=1), desde_inicio=timedelta(hours=9, minutes=59))
        self.assertTrue(self.sigue_dentro(self.client))
        self.envejecer(self.client, inactivo=timedelta(minutes=1), desde_inicio=timedelta(hours=10, minutes=1))
        registro = self.registro(self.client)
        self.assertFalse(self.sigue_dentro(self.client))
        registro.refresh_from_db()
        self.assertEqual(registro.motivo_cierre, MotivoCierre.MAXIMO)


class CerrarSesionesTests(SesionBase):
    def test_cierra_todas_las_sesiones_del_usuario_y_solo_las_suyas(self):
        ana = self.crear("ana", "TC")
        beto = self.crear("beto", "VF")
        c1, c2 = self.entrar(ana, Client()), self.entrar(ana, Client())
        c3 = self.entrar(beto, Client())
        self.assertEqual(sesion.cerrar_sesiones(ana), 2)
        self.assertFalse(self.sigue_dentro(c1))
        self.assertFalse(self.sigue_dentro(c2))
        self.assertTrue(self.sigue_dentro(c3))
        self.assertEqual(
            set(Sesion.objects.filter(usuario=ana).values_list("motivo_cierre", flat=True)),
            {MotivoCierre.FORZADO},
        )

    def test_una_sesion_cerrada_no_revive_aunque_la_cookie_siga(self):
        ana = self.crear("ana", "TC")
        self.entrar(ana)
        cookie = self.client.cookies["sessionid"].value
        sesion.cerrar_sesiones(ana)
        otro = Client()
        otro.cookies["sessionid"] = cookie
        self.assertNotEqual(otro.get(INICIO).status_code, 200)

    def test_puede_volver_a_ingresar_despues_del_cierre(self):
        ana = self.crear("ana", "TC")
        self.entrar(ana)
        sesion.cerrar_sesiones(ana)
        self.entrar(ana)
        self.assertTrue(self.sigue_dentro(self.client))

    def test_sesion_sin_registro_se_rechaza(self):
        ana = self.crear("ana", "TC")
        self.entrar(ana)
        Sesion.objects.filter(usuario=ana).update(clave_hash="0" * 64)
        self.assertFalse(self.sigue_dentro(self.client))


class SesionRLSTests(RLSBase):
    def test_cada_usuario_ve_solo_sus_sesiones(self):
        ahora = timezone.now()
        Sesion.objects.create(usuario=self.ana, clave_hash="a" * 64, iniciada_en=ahora, ultima_actividad=ahora)
        Sesion.objects.create(usuario=self.beto, clave_hash="b" * 64, iniciada_en=ahora, ultima_actividad=ahora)
        with self.como_app(usuario_id=self.ana.pk):
            self.assertEqual(list(Sesion.objects.values_list("usuario_id", flat=True)), [self.ana.pk])
            self.assertEqual(sesion.cerrar_sesiones(self.beto), 0)
        with self.como_app():
            self.assertEqual(Sesion.objects.count(), 0)

    def test_flujo_completo_con_rls(self):
        with self.como_app():
            self.client.post(reverse("account_login"), {"login": "ana", "password": CLAVE})
            self.assertEqual(self.client.get(INICIO).status_code, 200)
            self.client.post(reverse("account_logout"))
            self.assertNotEqual(self.client.get(INICIO).status_code, 200)
        self.assertEqual(
            list(Sesion.objects.filter(usuario=self.ana).values_list("motivo_cierre", flat=True)),
            [MotivoCierre.SALIDA],
        )


class SesionesSimultaneasTests(SesionBase):
    def test_la_tercera_sesion_cierra_la_mas_antigua(self):
        ana = self.crear("ana", "TC")
        c1 = self.entrar(ana, Client())
        r1 = self.registro(c1)
        c2 = self.entrar(ana, Client())
        c3 = self.entrar(ana, Client())
        r1.refresh_from_db()
        self.assertEqual(r1.motivo_cierre, MotivoCierre.FORZADO)
        self.assertFalse(self.sigue_dentro(c1))
        self.assertTrue(self.sigue_dentro(c2))
        self.assertTrue(self.sigue_dentro(c3))
        self.assertEqual(Sesion.objects.filter(usuario=ana, cerrada_en__isnull=True).count(), 2)

    def test_dos_sesiones_conviven(self):
        ana = self.crear("ana", "TC")
        c1, c2 = self.entrar(ana, Client()), self.entrar(ana, Client())
        self.assertTrue(self.sigue_dentro(c1))
        self.assertTrue(self.sigue_dentro(c2))

    def test_el_limite_es_por_usuario(self):
        ana = self.crear("ana", "TC")
        beto = self.crear("beto", "VF")
        b1, b2 = self.entrar(beto, Client()), self.entrar(beto, Client())
        self.entrar(ana, Client())
        self.entrar(ana, Client())
        self.entrar(ana, Client())
        self.assertTrue(self.sigue_dentro(b1))
        self.assertTrue(self.sigue_dentro(b2))

    def test_sesiones_ya_cerradas_no_cuentan(self):
        ana = self.crear("ana", "TC")
        c1 = self.entrar(ana, Client())
        c1.post(reverse("account_logout"))
        c2, c3 = self.entrar(ana, Client()), self.entrar(ana, Client())
        self.assertTrue(self.sigue_dentro(c2))
        self.assertTrue(self.sigue_dentro(c3))


class ClearSiteDataTests(SesionBase):
    ESPERADO = '"cache", "cookies", "storage"'

    def test_logout_envia_clear_site_data(self):
        self.entrar(self.crear("ana", "TC"))
        respuesta = self.client.post(reverse("account_logout"))
        self.assertEqual(respuesta["Clear-Site-Data"], self.ESPERADO)

    def test_cierre_por_inactividad_envia_clear_site_data(self):
        self.entrar(self.crear("ana", "GC"))
        self.envejecer(self.client, inactivo=timedelta(minutes=16))
        respuesta = self.client.get(INICIO)
        self.assertEqual(respuesta["Clear-Site-Data"], self.ESPERADO)

    def test_sesion_cerrada_a_la_fuerza_envia_clear_site_data(self):
        ana = self.crear("ana", "TC")
        self.entrar(ana)
        sesion.cerrar_sesiones(ana)
        self.assertEqual(self.client.get(INICIO)["Clear-Site-Data"], self.ESPERADO)

    def test_respuestas_normales_no_lo_envian(self):
        self.assertNotIn("Clear-Site-Data", self.client.get(reverse("account_login")))
        respuesta = self.client.post(reverse("account_login"), {"login": "nadie", "password": CLAVE})
        self.assertNotIn("Clear-Site-Data", respuesta)
        self.entrar(self.crear("ana", "TC"))
        self.assertNotIn("Clear-Site-Data", self.client.get(INICIO))


class SesionesSimultaneasRLSTests(RLSBase):
    def test_limite_funciona_con_rls(self):
        with self.como_app():
            clientes = [Client(), Client(), Client()]
            for c in clientes:
                c.post(reverse("account_login"), {"login": "ana", "password": CLAVE})
            dentro = [c.get(INICIO).status_code == 200 for c in clientes]
        self.assertEqual(dentro, [False, True, True])


class LogoutSinCSRFTests(SesionBase):
    """Clear-Site-Data borra la cookie CSRF: reenviar la salida no debe dar 403."""

    def setUp(self):
        super().setUp()
        self.cliente = Client(enforce_csrf_checks=True)

    def test_reenviar_la_salida_sin_sesion_lleva_al_login(self):
        respuesta = self.cliente.post(reverse("account_logout"))
        self.assertRedirects(respuesta, reverse("account_login"), fetch_redirect_response=False)

    def test_con_sesion_la_salida_sin_csrf_sigue_negada(self):
        self.cliente.force_login(self.crear("ana", "PF"))
        self.assertEqual(self.cliente.post(reverse("account_logout")).status_code, 403)
        self.assertIn("_auth_user_id", self.cliente.session)

    def test_otros_formularios_sin_csrf_siguen_negados(self):
        respuesta = self.cliente.post(reverse("account_login"), {"login": "x", "password": "y"})
        self.assertEqual(respuesta.status_code, 403)
