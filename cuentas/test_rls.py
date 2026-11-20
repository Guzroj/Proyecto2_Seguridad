"""
Paso 4 · Row Level Security de las tablas de cuentas.

La base local corre como superusuario, que se salta RLS. Estas pruebas crean,
dentro de la transacción de cada prueba (se revierte al final), un rol sin
privilegios equivalente a app_runtime y ejecutan con SET ROLE.
"""

from contextlib import contextmanager

from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.urls import reverse

from cuentas import rls, services
from cuentas.models import EstadoCuenta, RolUsuario, Usuario
from cuentas.tests import CLAVE, crear_cuenta

ROL = "verdeazul_rls_prueba"


class RLSBase(TestCase):
    def setUp(self):
        self.ana = crear_cuenta("ana", EstadoCuenta.ACTIVA)
        self.beto = crear_cuenta("beto", EstadoCuenta.ACTIVA)
        self.pendiente = crear_cuenta("pendiente")
        services.agregar_rol(self.ana, "PF")  # rol sin segundo factor obligatorio
        services.agregar_rol(self.beto, "VF")
        with connection.cursor() as c:
            c.execute(f"CREATE ROLE {ROL} NOLOGIN NOSUPERUSER NOBYPASSRLS")
            c.execute(f"GRANT USAGE ON SCHEMA public TO {ROL}")
            c.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROL}")
            c.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROL}")
            c.execute(f"GRANT EXECUTE ON FUNCTION cuentas_id_para_login(text) TO {ROL}")

    @contextmanager
    def como_app(self, usuario_id=None, fase=None):
        with connection.cursor() as c:
            c.execute(f"SET ROLE {ROL}")
        rls.fijar_contexto(usuario_id=usuario_id, fase=fase)
        try:
            yield
        finally:
            with connection.cursor() as c:
                c.execute("RESET ROLE")
            rls.fijar_contexto()

    def ajeno(self, sql, params):
        """Ejecuta en un savepoint; devuelve filas afectadas o la excepción."""
        try:
            with transaction.atomic(), connection.cursor() as c:
                c.execute(sql, params)
                return c.rowcount
        except DatabaseError as exc:
            return exc


class AnonimoTests(RLSBase):
    def test_anonimo_no_ve_cuentas_ni_roles(self):
        with self.como_app():
            self.assertEqual(Usuario.objects.count(), 0)
            self.assertEqual(RolUsuario.objects.count(), 0)

    def test_funcion_de_login_solo_da_el_id_de_cuentas_activas(self):
        with self.como_app():
            self.assertEqual(rls.id_para_login("ana"), self.ana.pk)
            self.assertIsNone(rls.id_para_login("pendiente"))
            self.assertIsNone(rls.id_para_login("nadie"))
            self.assertIsNone(rls.id_para_login("ana' OR '1'='1"))
            # Sigue sin ver filas después de usar la función.
            self.assertEqual(Usuario.objects.count(), 0)

    def test_anonimo_no_crea_cuentas(self):
        with self.como_app():
            resultado = self.ajeno(
                "INSERT INTO cuentas_usuario (password, is_superuser, username, first_name, last_name,"
                " email, is_staff, is_active, date_joined, estado) VALUES"
                " ('x', true, 'intruso', '', '', '', true, true, now(), 'ACTIVA')", [])
            self.assertIsInstance(resultado, DatabaseError)


class AutenticadoTests(RLSBase):
    def test_autenticado_lee_cuentas_y_roles(self):
        with self.como_app(usuario_id=self.ana.pk):
            self.assertEqual(Usuario.objects.count(), 3)
            self.assertEqual(RolUsuario.objects.count(), 2)

    def test_modifica_su_propia_fila(self):
        with self.como_app(usuario_id=self.ana.pk):
            filas = self.ajeno("UPDATE cuentas_usuario SET last_login = now() WHERE id = %s", [self.ana.pk])
            self.assertEqual(filas, 1)

    def test_no_modifica_filas_ajenas(self):
        with self.como_app(usuario_id=self.ana.pk):
            filas = self.ajeno("UPDATE cuentas_usuario SET first_name = 'x' WHERE id = %s", [self.beto.pk])
            self.assertEqual(filas, 0)

    def test_no_se_sube_privilegios_en_su_propia_fila(self):
        cambios = [
            "is_superuser = true", "is_staff = true", "username = 'otro'",
            "estado = 'INACTIVA', is_active = false",
        ]
        for cambio in cambios:
            with self.subTest(cambio=cambio), self.como_app(usuario_id=self.ana.pk):
                resultado = self.ajeno(f"UPDATE cuentas_usuario SET {cambio} WHERE id = %s", [self.ana.pk])
                self.assertIsInstance(resultado, DatabaseError)

    def test_no_crea_roles(self):
        with self.como_app(usuario_id=self.ana.pk):
            resultado = self.ajeno(
                "INSERT INTO cuentas_rolusuario (usuario_id, rol, vigente_desde, creado_en)"
                " VALUES (%s, 'GC', now(), now())", [self.ana.pk])
            self.assertIsInstance(resultado, DatabaseError)


class SistemaTests(RLSBase):
    def test_fase_sistema_gestiona_cuentas_y_roles(self):
        with self.como_app(fase="sistema"):
            nuevo = Usuario.objects.create_user("nuevo", password=CLAVE)
            services.cambiar_estado(nuevo, EstadoCuenta.ACTIVA)
            services.agregar_rol(nuevo, "PF")
            self.assertEqual(RolUsuario.objects.filter(usuario=nuevo).count(), 1)


class LoginConRLSTests(RLSBase):
    """El flujo completo de allauth funciona con un rol sujeto a RLS (como app_runtime en Neon)."""

    def test_login_y_pagina_de_inicio(self):
        with self.como_app():
            respuesta = self.client.post(reverse("account_login"), {"login": "ana", "password": CLAVE})
            self.assertRedirects(respuesta, reverse("inicio"), fetch_redirect_response=False)
            inicio = self.client.get(reverse("inicio"))
            self.assertContains(inicio, "ana")
            self.assertContains(inicio, "PF")

    def test_login_fallido(self):
        with self.como_app():
            self.client.post(reverse("account_login"), {"login": "ana", "password": "incorrecta-123456"})
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_el_contexto_se_limpia_al_terminar_la_peticion(self):
        with self.como_app():
            self.client.post(reverse("account_login"), {"login": "ana", "password": CLAVE})
            self.client.get(reverse("inicio"))
            with connection.cursor() as c:
                c.execute("SELECT current_setting('app.usuario_id', true)")
                self.assertEqual(c.fetchone()[0], "")
            self.assertEqual(Usuario.objects.count(), 0)
