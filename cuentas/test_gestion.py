"""
Paso 7 · C15: AC crea, modifica y da de baja cuentas (AC·O7a CR, MO, BA).

El motor de la matriz llega en el paso 13: aquí el gancho autorizar se activa
en modo permisivo, salvo en las pruebas de fallo cerrado. La restricción "solo
AC" ya la aplica RLS en la base (pruebas *RLSTests).
"""

from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cuentas import services, sesion
from cuentas.models import EstadoCuenta, MotivoCierre, RolUsuario, Sesion, Usuario
from cuentas.selectores import roles_vigentes
from cuentas.test_rls import RLSBase
from cuentas.tests import CLAVE, crear_cuenta
from nucleo.errores import (
    AccesoDenegado,
    DatoInvalido,
    FueraDeAlcance,
    SeparacionDeFunciones,
    TransicionInvalida,
)

EVENTOS = []


def registrar(**evento):
    EVENTOS.append(evento)


GANCHOS = {"usuarios_con_rol": "cuentas.roles.usuarios_con_rol", "registrar_evento": "cuentas.test_gestion.registrar"}
PERMISIVO = override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=True, VERDEAZUL_GANCHOS=GANCHOS)
NUEVA_CLAVE = "otra-clave-muy-larga-456"


def ultimo(accion):
    return [e for e in EVENTOS if e["accion"] == accion][-1]


@PERMISIVO
class GestionBase(TestCase):
    def setUp(self):
        EVENTOS.clear()
        self.ac = crear_cuenta("admin_ac", EstadoCuenta.ACTIVA)
        services.agregar_rol(self.ac, "AC")

    def nueva(self, username="nueva", rol="PF", **datos):
        return services.crear_cuenta(self.ac, username=username, rol=rol, **datos)


class CrearCuentaTests(GestionBase):
    def test_rol_comun_queda_activa_con_su_rol_y_sin_contrasena(self):
        for rol in ("TC", "VF", "AC", "PF"):
            with self.subTest(rol=rol):
                cuenta = self.nueva(f"n_{rol.lower()}", rol, first_name="Rosa", email="rosa@example.com")
                self.assertEqual(cuenta.estado, EstadoCuenta.ACTIVA)
                self.assertTrue(cuenta.is_active)
                self.assertEqual(roles_vigentes(cuenta), {rol})
                self.assertFalse(cuenta.has_usable_password())

    def test_gc_y_ae_quedan_pendientes_sin_rol(self):
        for rol in ("GC", "AE"):
            with self.subTest(rol=rol):
                ventana = {"inicio": timezone.localdate(), "fin": timezone.localdate()} if rol == "AE" else {}
                cuenta = self.nueva(f"n_{rol.lower()}", rol, **ventana)
                self.assertEqual(cuenta.estado, EstadoCuenta.PENDIENTE)
                self.assertFalse(cuenta.is_active)
                self.assertEqual(roles_vigentes(cuenta), set())

    def test_registra_evento_sin_datos_personales(self):
        cuenta = self.nueva(first_name="Rosa", last_name="Mora", email="rosa@example.com")
        evento = ultimo("cuenta:crear")
        self.assertEqual(evento["actor"], self.ac)
        self.assertEqual(evento["recurso_id"], str(cuenta.pk))
        self.assertEqual(evento["despues"], {"estado": "ACTIVA", "rol": "PF"})
        self.assertNotIn("Rosa", str(evento))
        self.assertNotIn("rosa@example.com", str(evento))

    def test_datos_invalidos(self):
        crear_cuenta("existente")
        casos = [
            {"username": ""},
            {"username": "con espacio"},
            {"username": "x" * 151},
            {"username": "EXISTENTE"},  # solo cambia mayúsculas
            {"rol": "XX"},
            {"email": "no-es-correo"},
            {"first_name": "x" * 151},
        ]
        for caso in casos:
            with self.subTest(caso=caso):
                datos = {"username": "valida", "rol": "PF", **caso}
                with self.assertRaises(DatoInvalido):
                    services.crear_cuenta(self.ac, **datos)
        self.assertFalse(Usuario.objects.filter(username="valida").exists())

    @override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=False)
    def test_sin_motor_se_niega_y_queda_en_bitacora(self):
        with self.assertRaises(AccesoDenegado):
            self.nueva()
        self.assertFalse(Usuario.objects.filter(username="nueva").exists())
        self.assertEqual(ultimo("cuenta:crear")["resultado"], "DENEGADO")


class ModificarCuentaTests(GestionBase):
    def setUp(self):
        super().setUp()
        self.cuenta = self.nueva(first_name="Rosa", email="rosa@example.com")

    def test_modifica_nombre_y_correo(self):
        services.modificar_cuenta(self.ac, self.cuenta, first_name=" Rosa María ", email="rm@example.com")
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.first_name, "Rosa María")
        self.assertEqual(self.cuenta.email, "rm@example.com")
        self.assertEqual(ultimo("cuenta:modificar")["despues"], {"campos": ["email", "first_name"]})
        self.assertNotIn("rm@example.com", str(ultimo("cuenta:modificar")))

    def test_sin_cambios_no_registra_evento(self):
        services.modificar_cuenta(self.ac, self.cuenta, first_name="Rosa")
        self.assertFalse([e for e in EVENTOS if e["accion"] == "cuenta:modificar"])

    def test_no_modifica_su_propia_cuenta(self):
        with self.assertRaises(SeparacionDeFunciones):
            services.modificar_cuenta(self.ac, self.ac, first_name="Yo")
        self.assertEqual(ultimo("cuenta:modificar")["resultado"], "DENEGADO")

    def test_campos_fuera_de_la_lista_blanca(self):
        for campo, valor in [("estado", "ACTIVA"), ("username", "otro"), ("is_superuser", True),
                             ("is_staff", True), ("password", "x")]:
            with self.subTest(campo=campo), self.assertRaises(DatoInvalido):
                services.modificar_cuenta(self.ac, self.cuenta, **{campo: valor})
        self.cuenta.refresh_from_db()
        self.assertFalse(self.cuenta.is_superuser)

    def test_cuenta_inactiva_no_se_modifica(self):
        services.dar_de_baja(self.ac, self.cuenta)
        with self.assertRaises(TransicionInvalida):
            services.modificar_cuenta(self.ac, self.cuenta, first_name="Otra")


class BajaTests(GestionBase):
    def test_sin_registros_pendientes_queda_inactiva(self):
        cuenta = services.dar_de_baja(self.ac, self.nueva())
        self.assertEqual(cuenta.estado, EstadoCuenta.INACTIVA)
        self.assertFalse(cuenta.is_active)
        evento = ultimo("cuenta:baja")
        self.assertEqual(evento["antes"], {"estado": "ACTIVA"})
        self.assertEqual(evento["despues"]["estado"], "INACTIVA")

    def test_con_registros_sin_sincronizar_queda_en_baja(self):
        cuenta = self.nueva("tecnico", "TC")
        with mock.patch("cuentas.selectores.registros_sin_sincronizar", return_value=True):
            cuenta = services.dar_de_baja(self.ac, cuenta)
        self.assertEqual(cuenta.estado, EstadoCuenta.EN_BAJA)
        self.assertFalse(cuenta.is_active)

    def test_pendiente_pasa_directo_a_inactiva(self):
        cuenta = self.nueva("gerente", "GC")
        with mock.patch("cuentas.selectores.registros_sin_sincronizar", return_value=True):
            cuenta = services.dar_de_baja(self.ac, cuenta)
        self.assertEqual(cuenta.estado, EstadoCuenta.INACTIVA)

    def test_en_baja_e_inactiva_no_se_dan_de_baja_otra_vez(self):
        # EN_BAJA solo pasa a INACTIVA por la sincronización final (C16).
        en_baja = self.nueva("tecnico", "TC")
        with mock.patch("cuentas.selectores.registros_sin_sincronizar", return_value=True):
            services.dar_de_baja(self.ac, en_baja)
        inactiva = services.dar_de_baja(self.ac, self.nueva())
        for cuenta in (en_baja, inactiva):
            with self.subTest(estado=cuenta.estado), self.assertRaises(TransicionInvalida):
                services.dar_de_baja(self.ac, cuenta)
        en_baja.refresh_from_db()
        self.assertEqual(en_baja.estado, EstadoCuenta.EN_BAJA)

    def test_no_se_da_de_baja_a_si_mismo(self):
        with self.assertRaises(SeparacionDeFunciones):
            services.dar_de_baja(self.ac, self.ac)
        self.ac.refresh_from_db()
        self.assertEqual(self.ac.estado, EstadoCuenta.ACTIVA)

    def test_cierra_todas_las_sesiones(self):
        cuenta = self.nueva()
        sesion.registrar_inicio(cuenta, "clave-1")
        sesion.registrar_inicio(cuenta, "clave-2")
        services.dar_de_baja(self.ac, cuenta)
        self.assertFalse(Sesion.objects.filter(usuario=cuenta, cerrada_en__isnull=True).exists())
        self.assertEqual(
            set(Sesion.objects.filter(usuario=cuenta).values_list("motivo_cierre", flat=True)),
            {MotivoCierre.FORZADO},
        )
        self.assertEqual(ultimo("cuenta:baja")["despues"]["sesiones_cerradas"], 2)

    def test_la_sesion_abierta_deja_de_servir(self):
        cuenta = crear_cuenta("rosa", EstadoCuenta.ACTIVA)
        services.agregar_rol(cuenta, "PF")
        self.client.post(reverse("account_login"), {"login": "rosa", "password": CLAVE})
        self.assertEqual(self.client.get(reverse("inicio")).status_code, 200)
        services.dar_de_baja(self.ac, cuenta)
        respuesta = self.client.get(reverse("inicio"))
        self.assertRedirects(respuesta, f"{reverse('account_login')}?next=/", fetch_redirect_response=False)
        # Tampoco puede volver a entrar.
        self.client.post(reverse("account_login"), {"login": "rosa", "password": CLAVE})
        self.assertEqual(self.client.get(reverse("inicio")).status_code, 302)


class EnlaceActivacionTests(GestionBase):
    def setUp(self):
        super().setUp()
        self.cuenta = self.nueva("rosa", "PF")

    def activar(self, enlace, clave=NUEVA_CLAVE):
        paso = self.client.get(enlace)
        self.assertEqual(paso.status_code, 302)  # el token sale de la URL y pasa a la sesión
        formulario = self.client.get(paso.url)
        self.assertTrue(formulario.context["validlink"])
        return self.client.post(paso.url, {"new_password1": clave, "new_password2": clave})

    def test_activa_y_luego_entra_por_el_login(self):
        respuesta = self.activar(services.enlace_activacion(self.ac, self.cuenta))
        self.assertRedirects(respuesta, reverse("account_login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)  # no inicia sesión solo
        self.cuenta.refresh_from_db()
        self.assertTrue(self.cuenta.check_password(NUEVA_CLAVE))
        self.client.post(reverse("account_login"), {"login": "rosa", "password": NUEVA_CLAVE})
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(ultimo("cuenta:modificar")["detalle"], "enlace de activación emitido")

    def test_el_enlace_es_de_un_solo_uso(self):
        enlace = services.enlace_activacion(self.ac, self.cuenta)
        self.activar(enlace)
        paso = self.client.get(enlace)
        self.assertFalse(paso.context["validlink"])

    def test_contrasena_debil_se_rechaza(self):
        respuesta = self.activar(services.enlace_activacion(self.ac, self.cuenta), clave="corta")
        self.assertEqual(respuesta.status_code, 200)
        self.cuenta.refresh_from_db()
        self.assertFalse(self.cuenta.has_usable_password())

    def test_enlaces_invalidos_dan_la_misma_respuesta(self):
        enlace = services.enlace_activacion(self.ac, self.cuenta)
        otra = self.nueva("otra")
        alterados = [
            enlace.replace(enlace.split("/")[-2], "token-falso"),
            reverse("cuenta_activar", kwargs={"uidb64": "MTIzNDU2", "token": enlace.split("/")[-2]}),
            reverse("cuenta_activar", kwargs={"uidb64": "%%%", "token": "x"}),
            enlace.replace(enlace.split("/")[-3], services.enlace_activacion(self.ac, otra).split("/")[-3]),
        ]
        for url in alterados:
            with self.subTest(url=url):
                respuesta = self.client.get(url)
                self.assertEqual(respuesta.status_code, 200)
                self.assertFalse(respuesta.context["validlink"])

    def test_enlace_vencido(self):
        enlace = services.enlace_activacion(self.ac, self.cuenta)
        despues = datetime.now() + timedelta(days=2)
        with mock.patch.object(PasswordResetTokenGenerator, "_now", return_value=despues):
            self.assertFalse(self.client.get(enlace).context["validlink"])

    def test_cuenta_con_contrasena_no_recibe_enlace(self):
        # AC nunca puede tomar una cuenta que ya está en uso.
        self.activar(services.enlace_activacion(self.ac, self.cuenta))
        with self.assertRaises(TransicionInvalida):
            services.enlace_activacion(self.ac, self.cuenta)

    def test_cuenta_dada_de_baja_no_se_activa(self):
        enlace = services.enlace_activacion(self.ac, self.cuenta)
        services.dar_de_baja(self.ac, self.cuenta)
        self.assertFalse(self.client.get(enlace).context["validlink"])
        with self.assertRaises(TransicionInvalida):
            services.enlace_activacion(self.ac, self.cuenta)

    def test_pendiente_fija_contrasena_pero_no_entra_hasta_activarse(self):
        gerente = self.nueva("gerente", "GC")
        self.activar(services.enlace_activacion(self.ac, gerente))
        self.client.post(reverse("account_login"), {"login": "gerente", "password": NUEVA_CLAVE})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_no_emite_enlace_para_si_mismo(self):
        with self.assertRaises(SeparacionDeFunciones):
            services.enlace_activacion(self.ac, self.ac)

    def test_con_sesion_iniciada_no_se_usa(self):
        enlace = services.enlace_activacion(self.ac, self.cuenta)
        self.client.force_login(crear_cuenta("otra_persona", EstadoCuenta.ACTIVA))
        self.assertRedirects(self.client.get(enlace), reverse("inicio"), fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# RLS: la base aplica "solo AC" y u ≠ s aunque la aplicación se equivoque
# ---------------------------------------------------------------------------
@PERMISIVO
class GestionRLSTests(RLSBase):
    def setUp(self):
        super().setUp()
        EVENTOS.clear()
        self.ac = crear_cuenta("admin_ac", EstadoCuenta.ACTIVA)
        services.agregar_rol(self.ac, "AC")
        self.admin = Usuario.objects.create_superuser("raiz", password=CLAVE)

    def falla(self, funcion, *args, **kwargs):
        with self.assertRaises(DatabaseError), transaction.atomic():
            funcion(*args, **kwargs)

    def test_ac_crea_modifica_y_da_de_baja(self):
        with self.como_app(usuario_id=self.ac.pk):
            cuenta = services.crear_cuenta(self.ac, username="nueva", rol="TC")
            self.assertEqual(roles_vigentes(cuenta), {"TC"})
            services.modificar_cuenta(self.ac, cuenta, first_name="Nueva")
            services.dar_de_baja(self.ac, cuenta)
        cuenta.refresh_from_db()
        self.assertEqual((cuenta.estado, cuenta.first_name), (EstadoCuenta.INACTIVA, "Nueva"))

    def test_otros_roles_no_crean_ni_modifican_cuentas(self):
        with self.como_app(usuario_id=self.beto.pk):  # VF
            self.falla(services.crear_cuenta, self.beto, username="intrusa", rol="PF")
            with self.assertRaises(FueraDeAlcance):  # no la puede bloquear: 404
                services.dar_de_baja(self.beto, self.ana)
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.estado, EstadoCuenta.ACTIVA)
        self.assertFalse(Usuario.objects.filter(username="intrusa").exists())

    def test_ac_no_crea_cuentas_privilegiadas_ni_activas_por_sql(self):
        insertar = (
            "INSERT INTO cuentas_usuario (password, is_superuser, username, first_name, last_name,"
            " email, is_staff, is_active, date_joined, estado) VALUES ('!', %s, %s, '', '', '', %s, %s, now(), %s)"
        )
        casos = [(True, "s1", False, False, "PENDIENTE"), (False, "s2", True, False, "PENDIENTE"),
                 (False, "s3", False, True, "ACTIVA")]
        with self.como_app(usuario_id=self.ac.pk):
            for caso in casos:
                with self.subTest(caso=caso):
                    self.assertIsInstance(self.ajeno(insertar, list(caso)), DatabaseError)

    def test_ac_no_toca_superusuarios_ni_da_privilegios(self):
        with self.como_app(usuario_id=self.ac.pk):
            self.assertEqual(self.ajeno("UPDATE cuentas_usuario SET first_name = 'x' WHERE id = %s", [self.admin.pk]), 0)
            self.assertIsInstance(
                self.ajeno("UPDATE cuentas_usuario SET is_staff = true WHERE id = %s", [self.ana.pk]), DatabaseError)

    def test_nadie_cambia_contrasena_ni_username_ajenos(self):
        with self.como_app(usuario_id=self.ac.pk):
            for cambio in ("password = 'argon2$x'", "username = 'otra'"):
                with self.subTest(cambio=cambio):
                    resultado = self.ajeno(f"UPDATE cuentas_usuario SET {cambio} WHERE id = %s", [self.ana.pk])
                    self.assertIsInstance(resultado, DatabaseError)

    def test_ac_no_gestiona_su_propia_cuenta_ni_se_da_roles(self):
        with self.como_app(usuario_id=self.ac.pk):
            self.assertIsInstance(
                self.ajeno("UPDATE cuentas_usuario SET estado = 'INACTIVA', is_active = false WHERE id = %s",
                           [self.ac.pk]), DatabaseError)
            self.assertIsInstance(
                self.ajeno("INSERT INTO cuentas_rolusuario (usuario_id, rol, vigente_desde, creado_en)"
                           " VALUES (%s, 'GC', now(), now())", [self.ac.pk]), DatabaseError)

    def test_baja_cierra_sesiones_y_solo_ac_las_ve(self):
        sesion.registrar_inicio(self.ana, "clave-ana")
        with self.como_app(usuario_id=self.beto.pk):  # VF no ve ni cierra sesiones ajenas
            self.assertEqual(Sesion.objects.filter(usuario=self.ana).count(), 0)
            self.assertEqual(sesion.cerrar_sesiones(self.ana), 0)
        with self.como_app(usuario_id=self.ac.pk):
            self.assertEqual(Sesion.objects.filter(usuario=self.ana).count(), 1)
            services.dar_de_baja(self.ac, self.ana)
        self.assertFalse(Sesion.objects.filter(usuario=self.ana, cerrada_en__isnull=True).exists())

    def test_rol_ac_vencido_o_en_preingreso_no_gestiona(self):
        with self.como_app(usuario_id=self.ac.pk, fase="preingreso"):
            self.falla(services.crear_cuenta, self.ac, username="pre", rol="PF")
        services.terminar_rol(self.ac, "AC")
        with self.como_app(usuario_id=self.ac.pk):
            self.falla(services.crear_cuenta, self.ac, username="vencido", rol="PF")
        self.assertFalse(RolUsuario.objects.filter(usuario__username__in=["pre", "vencido"]).exists())

    def test_activacion_con_rls(self):
        with self.como_app(usuario_id=self.ac.pk):
            cuenta = services.crear_cuenta(self.ac, username="rosa", rol="PF")
            enlace = services.enlace_activacion(self.ac, cuenta)
        with self.como_app():
            paso = self.client.get(enlace)
            self.client.post(paso.url, {"new_password1": NUEVA_CLAVE, "new_password2": NUEVA_CLAVE})
            self.client.post(reverse("account_login"), {"login": "rosa", "password": NUEVA_CLAVE})
            self.assertIn("_auth_user_id", self.client.session)
