"""
Paso 17c · RLS de las tablas de Django y allauth: correos, confirmaciones y
bitácora del admin por sujeto; metadatos de solo lectura para la app; la sesión
de Django sin RLS a propósito. Incluye un ingreso completo con el rol de la app.
"""

from contextlib import contextmanager

from allauth.account.models import EmailAddress, EmailConfirmation
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.urls import reverse

from cuentas import rls, services
from cuentas.models import EstadoCuenta
from cuentas.rls import FASE_PREINGRESO
from cuentas.tests import CLAVE, crear_cuenta

ROL = "verdeazul_app_prueba"
METADATOS = ["auth_group", "auth_group_permissions", "auth_permission", "django_content_type", "django_migrations"]


class RLSDjangoBase(TestCase):
    def setUp(self):
        self.ana = crear_cuenta("ana", EstadoCuenta.ACTIVA)
        self.beto = crear_cuenta("beto", EstadoCuenta.ACTIVA)
        self.admin_ac = crear_cuenta("admin_ac", EstadoCuenta.ACTIVA)
        services.agregar_rol(self.ana, "PF")
        services.agregar_rol(self.beto, "PF")
        services.agregar_rol(self.admin_ac, "AC")
        self.correo_ana = EmailAddress.objects.create(user=self.ana, email="ana@example.com", primary=True)
        self.correo_beto = EmailAddress.objects.create(user=self.beto, email="beto@example.com", primary=True)
        self.confirmacion = EmailConfirmation.create(self.correo_ana)
        tipo = ContentType.objects.get_for_model(EmailAddress)
        self.registro = LogEntry.objects.create(user=self.beto, content_type=tipo, object_id="1",
                                                object_repr="x", action_flag=ADDITION, change_message="")
        with connection.cursor() as c:
            c.execute(f"CREATE ROLE {ROL} NOLOGIN NOSUPERUSER NOBYPASSRLS")
            c.execute(f"GRANT USAGE ON SCHEMA public TO {ROL}")
            c.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROL}")
            c.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROL}")
            c.execute(f"GRANT EXECUTE ON FUNCTION cuentas_id_para_login(text) TO {ROL}")

    @contextmanager
    def como_app(self):
        """Conexión como la de app_runtime (session_user = current_user = rol de la app)."""
        with connection.cursor() as c:
            c.execute(f"SET SESSION AUTHORIZATION {ROL}")
        try:
            yield
        finally:
            with connection.cursor() as c:
                c.execute("RESET SESSION AUTHORIZATION")
            rls.fijar_contexto()

    @contextmanager
    def como(self, usuario=None, fase=None):
        with self.como_app():
            rls.fijar_contexto(usuario_id=getattr(usuario, "pk", None), fase=fase)
            yield

    def sql(self, consulta, params=()):
        try:
            with transaction.atomic(), connection.cursor() as c:
                c.execute(consulta, params)
                return c.rowcount
        except DatabaseError as exc:
            self.assertIn("row-level security", str(exc))
            return exc


class CorreosTests(RLSDjangoBase):
    def test_cada_quien_ve_su_correo_y_ac_todos(self):
        with self.como(self.ana):
            self.assertEqual(set(EmailAddress.objects.values_list("email", flat=True)), {"ana@example.com"})
            self.assertEqual(EmailConfirmation.objects.count(), 1)
        with self.como(self.ana, fase=FASE_PREINGRESO):  # como su cuenta, también antes del segundo factor
            self.assertEqual(EmailAddress.objects.count(), 1)
        with self.como(self.beto):
            self.assertEqual(set(EmailAddress.objects.values_list("email", flat=True)), {"beto@example.com"})
            self.assertEqual(EmailConfirmation.objects.count(), 0)
        with self.como(self.admin_ac):
            self.assertEqual(EmailAddress.objects.count(), 2)
        with self.como():
            self.assertEqual(EmailAddress.objects.count(), 0)
            self.assertEqual(EmailConfirmation.objects.count(), 0)

    def test_nadie_toca_el_correo_ajeno_ni_borra(self):
        with self.como(self.beto):
            self.assertEqual(self.sql("UPDATE account_emailaddress SET verified = true WHERE id = %s",
                                      [self.correo_ana.pk]), 0)
            self.assertIsInstance(self.sql(
                "INSERT INTO account_emailaddress (email, verified, \"primary\", user_id) VALUES ('x@x.com', false, false, %s)",
                [self.ana.pk]), DatabaseError)
            self.assertIsInstance(self.sql(
                "INSERT INTO account_emailconfirmation (created, key, email_address_id) VALUES (now(), 'k', %s)",
                [self.correo_ana.pk]), DatabaseError)
            self.assertEqual(self.sql("UPDATE account_emailaddress SET verified = true WHERE id = %s",
                                      [self.correo_beto.pk]), 1)
        with self.como(self.admin_ac):
            self.assertEqual(self.sql("UPDATE account_emailaddress SET verified = true"), 2)
            self.assertEqual(self.sql("DELETE FROM account_emailaddress"), 0)
            self.assertEqual(self.sql("DELETE FROM account_emailconfirmation"), 0)


class BitacoraAdminTests(RLSDjangoBase):
    def test_solo_las_acciones_propias(self):
        with self.como(self.beto):
            self.assertEqual(LogEntry.objects.count(), 1)
            self.assertEqual(self.sql("UPDATE django_admin_log SET change_message = 'x'"), 0)
            self.assertEqual(self.sql("DELETE FROM django_admin_log"), 0)
        with self.como(self.ana):
            self.assertEqual(LogEntry.objects.count(), 0)
            self.assertIsInstance(self.sql(
                "INSERT INTO django_admin_log (action_time, object_repr, action_flag, change_message, user_id) "
                "VALUES (now(), 'x', 1, '', %s)", [self.beto.pk]), DatabaseError)


class MetadatosTests(RLSDjangoBase):
    def test_la_app_solo_los_lee(self):
        with self.como(self.admin_ac):
            self.assertGreater(ContentType.objects.count(), 0)
            for tabla in METADATOS:
                with self.subTest(tabla=tabla):
                    self.assertEqual(self.sql(f"DELETE FROM {tabla}"), 0)
                    columna = "name" if tabla in ("auth_group", "auth_permission", "django_migrations") else "id"
                    self.assertEqual(self.sql(f"UPDATE {tabla} SET {columna} = {columna}"), 0)
            self.assertIsInstance(self.sql("INSERT INTO auth_group (name) VALUES ('GC')"), DatabaseError)
            self.assertIsInstance(self.sql(
                "INSERT INTO django_migrations (app, name, applied) VALUES ('x', 'y', now())"), DatabaseError)

    def test_estado_de_rls_por_tabla(self):
        with connection.cursor() as c:
            c.execute("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                      "WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace")
            estado = {nombre: (activo, forzado) for nombre, activo, forzado in c.fetchall()}
        for tabla in METADATOS:  # sin FORCE: `migrate` (dueño) los escribe
            self.assertEqual(estado[tabla], (True, False), tabla)
        self.assertEqual(estado["django_session"], (False, False))  # a propósito: se carga por su clave
        sin_rls = sorted(t for t, (activo, _) in estado.items() if not activo)
        self.assertEqual(sin_rls, ["django_session"])
        con_force = {t for t, (_, forzado) in estado.items() if forzado}
        self.assertEqual(con_force, set(estado) - set(METADATOS) - {"django_session"})


class IngresoComoAppTests(RLSDjangoBase):
    def test_login_y_pagina_de_inicio_con_el_rol_de_la_app(self):
        with self.como_app():
            respuesta = self.client.post(reverse("account_login"), {"login": "ana", "password": CLAVE})
            self.assertRedirects(respuesta, reverse("inicio"), fetch_redirect_response=False)
            self.assertEqual(self.client.get(reverse("inicio")).status_code, 200)
            fallida = self.client_class().post(reverse("account_login"), {"login": "beto", "password": "otra-clave-larga"})
            self.assertEqual(fallida.status_code, 200)
