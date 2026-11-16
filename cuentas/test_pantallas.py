"""
Paso 18a · Pantallas de cuentas (O7a) y "Mi cuenta", de punta a punta: login con
segundo factor, motor real, servicios y bitácora. Las vistas no deciden permisos:
se comprueba que el motor los aplica y que los errores se ven como 403/404/400/409.
"""

import os
from unittest import mock

from django.test import Client, override_settings
from django.urls import reverse

from autorizacion.test_cliente import LOCAL, MotorFalso
from autorizacion.test_integracion_o7_o9 import GANCHOS
from cuentas.models import EstadoCuenta, Usuario
from cuentas.selectores import roles_vigentes
from cuentas.test_gestion import EVENTOS, ultimo
from cuentas.test_mfa import MFABase, codigo_actual
from cuentas.tests import CLAVE


@override_settings(DEBUG=True, VERDEAZUL_GANCHOS=GANCHOS)
class PantallasBase(MFABase):
    def setUp(self):
        super().setUp()
        EVENTOS.clear()
        for parche in (MotorFalso().instalar(), mock.patch.dict(os.environ, LOCAL)):
            parche.start()
            self.addCleanup(parche.stop)
        self.ac = self.crear("admin_ac", "AC", totp=True)
        self.gc = self.crear("gerente", "GC", totp=True)
        self.tc = self.crear("tecnica", "TC", totp=True)
        self.pf = self.crear("propietaria", "PF")

    def entrar(self, usuario) -> Client:
        cliente = Client()
        cliente.post(reverse("account_login"), {"login": usuario.username, "password": CLAVE})
        if usuario.pk != self.pf.pk:
            cliente.post(reverse("mfa_authenticate"), {"code": codigo_actual()})
        self.assertEqual(cliente.get(reverse("inicio")).status_code, 200)
        return cliente


class MenuYListaTests(PantallasBase):
    def test_menu_segun_los_roles_vigentes(self):
        self.assertContains(self.entrar(self.ac).get(reverse("inicio")), reverse("gestion_cuentas"))
        cliente = self.entrar(self.pf)
        self.assertNotContains(cliente.get(reverse("inicio")), reverse("gestion_cuentas"))
        self.assertContains(cliente.get(reverse("inicio")), reverse("mi_cuenta"))

    def test_lista_filtrada_por_el_motor(self):
        respuesta = self.entrar(self.ac).get(reverse("gestion_cuentas"))
        for u in (self.ac, self.gc, self.tc, self.pf):
            self.assertContains(respuesta, u.username)
        self.assertContains(respuesta, reverse("gestion_cuenta_nueva"))
        propia = self.entrar(self.tc).get(reverse("gestion_cuentas"))            # C21: solo la suya
        self.assertContains(propia, "tecnica")
        self.assertNotContains(propia, "admin_ac")
        self.assertNotContains(propia, reverse("gestion_cuenta_nueva"))

    def test_sin_sesion_va_al_login(self):
        respuesta = Client().get(reverse("gestion_cuentas"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse("account_login"), respuesta["Location"])


class FichaTests(PantallasBase):
    def test_ac_ve_la_ficha_y_las_acciones(self):
        cliente = self.entrar(self.ac)  # un solo ingreso: cada código TOTP sirve una vez
        respuesta = cliente.get(reverse("gestion_cuenta", args=[self.tc.pk]))
        self.assertContains(respuesta, "Cuenta tecnica")
        self.assertContains(respuesta, reverse("gestion_cuenta_baja", args=[self.tc.pk]))
        propia = cliente.get(reverse("gestion_cuenta", args=[self.ac.pk]))
        self.assertNotContains(propia, reverse("gestion_cuenta_baja", args=[self.ac.pk]))  # C15: no la suya

    def test_fuera_de_alcance_es_404_igual_que_inexistente_y_queda_en_bitacora(self):
        cliente = self.entrar(self.tc)
        ajena = cliente.get(reverse("gestion_cuenta", args=[self.ac.pk]))
        inexistente = cliente.get(reverse("gestion_cuenta", args=[999999]))
        self.assertEqual((ajena.status_code, inexistente.status_code), (404, 404))
        self.assertEqual(ajena.content, inexistente.content)
        evento = ultimo("cuenta:leer")
        self.assertEqual((evento["resultado"], evento["recurso_id"], evento["detalle"]),
                         ("DENEGADO", str(self.ac.pk), "FueraDeAlcance"))


class AltaTests(PantallasBase):
    def test_ac_crea_una_cuenta(self):
        respuesta = self.entrar(self.ac).post(reverse("gestion_cuenta_nueva"), {
            "username": "nuevo_tc", "rol": "TC", "first_name": "Nuevo", "email": "n@example.com"})
        nueva = Usuario.objects.get(username="nuevo_tc")
        self.assertRedirects(respuesta, reverse("gestion_cuenta", args=[nueva.pk]))
        self.assertEqual((nueva.estado, roles_vigentes(nueva)), (EstadoCuenta.ACTIVA, {"TC"}))
        self.assertFalse(nueva.has_usable_password())

    def test_datos_invalidos_vuelven_al_formulario(self):
        cliente = self.entrar(self.ac)
        repetido = cliente.post(reverse("gestion_cuenta_nueva"), {"username": "TECNICA", "rol": "PF"})
        self.assertContains(repetido, "ya existe una cuenta", status_code=400)
        sin_ventana = cliente.post(reverse("gestion_cuenta_nueva"), {"username": "auditora", "rol": "AE"})
        self.assertEqual(sin_ventana.status_code, 400)
        self.assertFalse(Usuario.objects.filter(username="auditora").exists())

    def test_solo_ac(self):
        respuesta = self.entrar(self.gc).post(reverse("gestion_cuenta_nueva"), {"username": "x", "rol": "PF"})
        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(Usuario.objects.filter(username="x").exists())
        self.assertEqual(ultimo("cuenta:crear")["resultado"], "DENEGADO")


class CambiosTests(PantallasBase):
    def test_modificar(self):
        cliente = self.entrar(self.ac)
        url = reverse("gestion_cuenta_modificar", args=[self.tc.pk])
        self.assertContains(cliente.get(url), 'name="first_name"')
        self.assertRedirects(cliente.post(url, {"first_name": "Tere", "last_name": "Mora", "email": ""}),
                             reverse("gestion_cuenta", args=[self.tc.pk]))
        self.tc.refresh_from_db()
        self.assertEqual(self.tc.get_full_name(), "Tere Mora")
        self.assertEqual(cliente.post(reverse("gestion_cuenta_modificar", args=[self.ac.pk]),
                                      {"first_name": "Yo"}).status_code, 403)          # C15

    def test_baja_con_confirmacion(self):
        cliente = self.entrar(self.ac)
        url = reverse("gestion_cuenta_baja", args=[self.pf.pk])
        self.assertContains(cliente.get(url), "Dar de baja")
        self.assertEqual(Usuario.objects.get(pk=self.pf.pk).estado, EstadoCuenta.ACTIVA)  # GET no cambia nada
        cliente.post(url)
        self.assertEqual(Usuario.objects.get(pk=self.pf.pk).estado, EstadoCuenta.INACTIVA)
        self.assertEqual(cliente.post(url).status_code, 409)                          # ya no se da de baja

    def test_enlace_de_activacion(self):
        cliente = self.entrar(self.ac)
        cliente.post(reverse("gestion_cuenta_nueva"), {"username": "nueva_pf", "rol": "PF"})
        nueva = Usuario.objects.get(username="nueva_pf")
        url = reverse("gestion_cuenta_enlace", args=[nueva.pk])
        self.assertEqual(cliente.get(url).status_code, 405)                            # solo POST
        respuesta = cliente.post(url)
        self.assertContains(respuesta, "http://testserver/cuentas/activar/")
        self.assertEqual((respuesta["Cache-Control"], respuesta["Referrer-Policy"]), ("no-store", "no-referrer"))
        self.assertEqual(cliente.post(reverse("gestion_cuenta_enlace", args=[self.tc.pk])).status_code, 409)


class MiCuentaTests(PantallasBase):
    def test_mi_cuenta_y_mis_sesiones(self):
        self.entrar(self.pf)
        respuesta = self.entrar(self.pf).get(reverse("mi_cuenta"))
        self.assertContains(respuesta, "propietaria")
        self.assertEqual(len(respuesta.context["sesiones"]), 2)
        self.assertTrue(all(s.usuario_id == self.pf.pk for s in respuesta.context["sesiones"]))
        self.assertEqual(len(self.entrar(self.ac).get(reverse("mi_cuenta")).context["sesiones"]), 1)
