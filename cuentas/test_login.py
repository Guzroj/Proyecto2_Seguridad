"""Contraseñas Argon2id y login con django-allauth."""

import re

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from cuentas import services
from cuentas.models import EstadoCuenta, Usuario
from cuentas.tests import CLAVE, crear_cuenta


class ContrasenasTests(TestCase):
    def test_hash_es_argon2id(self):
        cuenta = crear_cuenta()
        self.assertTrue(cuenta.password.startswith("argon2$argon2id$"))

    def test_minimo_doce_caracteres(self):
        with self.assertRaises(ValidationError):
            validate_password("Corta-123ab")  # 11
        validate_password("Larga-123abc")  # 12

    def test_hash_antiguo_se_actualiza_a_argon2_al_ingresar(self):
        cuenta = crear_cuenta(estado=EstadoCuenta.ACTIVA)
        Usuario.objects.filter(pk=cuenta.pk).update(
            password=make_password(CLAVE, hasher="pbkdf2_sha256")
        )
        self.assertIsNotNone(authenticate(username=cuenta.username, password=CLAVE))
        cuenta.refresh_from_db()
        self.assertTrue(cuenta.password.startswith("argon2$argon2id$"))


class LoginTests(TestCase):
    def setUp(self):
        self.url = reverse("account_login")
        self.ana = crear_cuenta("ana", EstadoCuenta.ACTIVA)
        services.agregar_rol(self.ana, "PF")  # rol sin segundo factor obligatorio

    def entrar(self, username, clave=CLAVE):
        return self.client.post(self.url, {"login": username, "password": clave})

    @staticmethod
    def sin_variables(contenido: bytes, username: str) -> bytes:
        contenido = re.sub(rb'name="csrfmiddlewaretoken" value="[^"]+"', b"", contenido)
        return contenido.replace(username.encode(), b"")

    def test_formulario_de_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_login_correcto_lleva_al_inicio(self):
        respuesta = self.entrar("ana")
        self.assertRedirects(respuesta, reverse("inicio"))
        inicio = self.client.get(reverse("inicio"))
        self.assertContains(inicio, "ana")
        self.assertContains(inicio, "PF")

    def test_clave_incorrecta(self):
        respuesta = self.entrar("ana", "otra-clave-cualquiera")
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_cuentas_no_activas_no_ingresan_y_no_se_distinguen(self):
        error_inexistente = self.sin_variables(self.entrar("nadie").content, "nadie")
        for estado in (EstadoCuenta.PENDIENTE, EstadoCuenta.EN_BAJA, EstadoCuenta.INACTIVA):
            with self.subTest(estado=estado):
                c = crear_cuenta(f"c-{estado.lower()}", estado)
                respuesta = self.entrar(c.username)
                self.assertEqual(respuesta.status_code, 200)
                self.assertNotIn("_auth_user_id", self.client.session)
                # Misma respuesta que una cuenta inexistente (sin enumeración).
                self.assertEqual(self.sin_variables(respuesta.content, c.username), error_inexistente)

    def test_identificador_de_sesion_se_renueva_al_ingresar(self):
        self.client.get(self.url)
        antes = self.client.session.session_key
        self.entrar("ana")
        self.assertNotEqual(self.client.session.session_key, antes)

    def test_inicio_exige_sesion(self):
        respuesta = self.client.get(reverse("inicio"))
        self.assertRedirects(respuesta, f"{self.url}?next=/", fetch_redirect_response=False)

    def test_logout(self):
        self.entrar("ana")
        self.client.post(reverse("account_logout"))
        self.assertNotIn("_auth_user_id", self.client.session)


class RutasCerradasTests(TestCase):
    def test_sin_registro_publico(self):
        self.client.post(
            reverse("account_signup"),
            {"username": "intruso", "password1": "clave-de-prueba-larga-123"},
        )
        self.assertFalse(Usuario.objects.filter(username="intruso").exists())

    def test_restablecer_contrasena_y_correos_no_existen(self):
        for ruta in ("/cuentas/password/reset/", "/cuentas/password/reset/done/", "/cuentas/email/"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 404)


class MenuTests(TestCase):
    """El menú de las pantallas de allauth no ofrece rutas deshabilitadas."""

    def test_sin_cambiar_correo_con_sesion(self):
        u = crear_cuenta("ana", EstadoCuenta.ACTIVA)
        services.agregar_rol(u, "PF")
        self.client.force_login(u)
        respuesta = self.client.get(reverse("account_change_password"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'href="/cuentas/email/')
        self.assertNotContains(respuesta, "orreo electrónico")
        self.assertContains(respuesta, f'href="{reverse("account_logout")}"')
        self.assertContains(respuesta, f'href="{reverse("mfa_index")}"')

    def test_sin_registrarse_sin_sesion(self):
        respuesta = self.client.get(reverse("account_login"))
        self.assertNotContains(respuesta, f'href="{reverse("account_signup")}"')
        self.assertNotContains(respuesta, 'href="/cuentas/email/')
