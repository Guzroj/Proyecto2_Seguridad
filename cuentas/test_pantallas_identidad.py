"""
Paso 18c · Pantallas de dispositivos de campo (O7c, C18), restablecimiento del
segundo factor (O7d, C17), alertas (O9, C22) y cierre de la baja de un técnico
(C16), de punta a punta con login, motor real y servicios. Incluye V14.
"""

from datetime import timedelta

from allauth.mfa.models import Authenticator
from django.urls import reverse
from django.utils import timezone

from cuentas import services
from cuentas.models import (
    Alerta,
    ControlHBAC,
    DispositivoCampo,
    EstadoAlerta,
    EstadoCuenta,
    EstadoDispositivo,
    EstadoRestablecimiento,
    RestablecimientoFactor,
    Usuario,
)
from cuentas.test_dispositivos import ed25519_pem
from cuentas.test_mfa import SECRETO
from cuentas.test_pantallas import PantallasBase


def hace(**delta) -> str:
    return timezone.localtime(timezone.now() - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M")


class DispositivosTests(PantallasBase):
    def test_ac_registra_y_revoca(self):
        cliente = self.entrar(self.ac)
        url = reverse("gestion_dispositivos")
        self.assertRedirects(cliente.post(url, {"responsable": self.tc.pk, "etiqueta": "Tableta 1",
                                                "llave_publica": ed25519_pem()}), url)
        d = DispositivoCampo.objects.get(responsable=self.tc)
        self.assertContains(cliente.get(url), "Tableta 1")
        revocar = reverse("gestion_dispositivo_revocar", args=[d.pk])
        self.assertContains(cliente.get(revocar), "definitivo")
        cliente.post(revocar, {"motivo": "Extraviada"})
        d.refresh_from_db()
        self.assertEqual((d.estado, d.motivo_revocacion), (EstadoDispositivo.REVOCADO, "Extraviada"))

    def test_datos_invalidos_y_responsable_sin_tc(self):
        cliente = self.entrar(self.ac)
        url = reverse("gestion_dispositivos")
        self.assertEqual(cliente.post(url, {"responsable": self.pf.pk, "etiqueta": "X",
                                            "llave_publica": ed25519_pem()}).status_code, 400)  # no está en la lista
        self.assertContains(cliente.post(url, {"responsable": self.tc.pk, "etiqueta": "X",
                                               "llave_publica": "no es una llave"}), "llave", status_code=400)
        self.assertFalse(DispositivoCampo.objects.exists())

    def test_tc_no_entra(self):
        self.assertEqual(self.entrar(self.tc).get(reverse("gestion_dispositivos")).status_code, 403)


class RestablecimientoTests(PantallasBase):
    def solicitar(self, cuenta):
        cliente = self.entrar(self.ac)
        self.assertContains(cliente.get(reverse("gestion_cuenta", args=[cuenta.pk])),
                            reverse("gestion_restablecimiento_solicitar", args=[cuenta.pk]))
        cliente.post(reverse("gestion_restablecimiento_solicitar", args=[cuenta.pk]), {"motivo": "Perdió el teléfono"})
        return RestablecimientoFactor.objects.get(usuario=cuenta)

    def test_gc_verifica_y_autoriza(self):
        r = self.solicitar(self.tc)
        cliente = self.entrar(self.gc)
        url = reverse("gestion_restablecimiento_autorizar", args=[r.pk])
        self.assertContains(cliente.get(reverse("gestion_restablecimientos")), url)
        pagina = cliente.get(url)
        self.assertContains(pagina, "totp")                                       # metadatos
        self.assertNotContains(pagina, SECRETO)                                   # nunca el secreto
        vieja = cliente.post(url, {"metodo_verificacion": "PRESENCIAL", "verificado_en": hace(days=8)})
        self.assertContains(vieja, "más de 7 días", status_code=400)
        cliente.post(url, {"metodo_verificacion": "PRESENCIAL", "verificado_en": hace(minutes=10)})
        r.refresh_from_db()
        self.assertEqual((r.estado, r.metodo_verificacion), (EstadoRestablecimiento.AUTORIZADO, "PRESENCIAL"))
        self.assertFalse(Authenticator.objects.filter(user=self.tc).exists())

    def test_rechazar_con_motivo(self):
        r = self.solicitar(self.pf)
        cliente = self.entrar(self.gc)
        url = reverse("gestion_restablecimiento_rechazar", args=[r.pk])
        self.assertEqual(cliente.post(url, {"motivo": ""}).status_code, 400)
        cliente.post(url, {"motivo": "No se pudo verificar"})
        r.refresh_from_db()
        self.assertEqual((r.estado, r.motivo_rechazo), (EstadoRestablecimiento.RECHAZADO, "No se pudo verificar"))

    def test_el_gc_no_ve_el_suyo(self):
        r = self.solicitar(self.gc)
        cliente = self.entrar(self.gc)
        self.assertNotContains(cliente.get(reverse("gestion_restablecimientos")), self.gc.username + "</td>")
        self.assertEqual(cliente.get(reverse("gestion_restablecimiento_autorizar", args=[r.pk])).status_code, 404)

    def test_tc_no_entra(self):
        self.assertEqual(self.entrar(self.tc).get(reverse("gestion_restablecimientos")).status_code, 403)


class AlertasTests(PantallasBase):
    def test_gc_resuelve_y_levanta_la_suspension(self):
        alerta, _ = services.crear_alerta(self.tc, ControlHBAC.H1, detalle={"accion": "finca:leer", "n": 26})
        cliente = self.entrar(self.gc)
        lista = cliente.get(reverse("gestion_alertas"))
        self.assertContains(lista, "suspende lecturas")
        self.assertContains(lista, "n=26")
        url = reverse("gestion_alerta_resolver", args=[alerta.pk])
        self.assertEqual(cliente.post(url, {"motivo": ""}).status_code, 400)
        cliente.post(url, {"motivo": "Cierre de período, revisado"})
        alerta.refresh_from_db()
        self.assertEqual((alerta.estado, alerta.resuelta_por), (EstadoAlerta.RESUELTA, self.gc))

    def test_v14_no_resuelve_la_suya(self):
        alerta, _ = services.crear_alerta(self.gc, ControlHBAC.H2)
        cliente = self.entrar(self.gc)
        url = reverse("gestion_alerta_resolver", args=[alerta.pk])
        self.assertNotContains(cliente.get(reverse("gestion_alertas")), url)
        self.assertEqual(cliente.post(url, {"motivo": "mía"}).status_code, 403)
        self.assertEqual(Alerta.objects.get(pk=alerta.pk).estado, EstadoAlerta.ABIERTA)

    def test_tc_no_ve_alertas(self):
        self.assertEqual(self.entrar(self.tc).get(reverse("gestion_alertas")).status_code, 403)


class CierreBajaTests(PantallasBase):
    def test_gc_cierra_la_baja_de_un_tc(self):
        services.registrar_dispositivo(self.ac, self.tc, etiqueta="Tableta", llave_publica=ed25519_pem())
        self.entrar(self.ac).post(reverse("gestion_cuenta_baja", args=[self.tc.pk]))
        self.assertEqual(Usuario.objects.get(pk=self.tc.pk).estado, EstadoCuenta.EN_BAJA)
        cliente = self.entrar(self.gc)
        url = reverse("gestion_cuenta_cerrar_baja", args=[self.tc.pk])
        self.assertContains(cliente.get(reverse("gestion_cuenta", args=[self.tc.pk])), url)
        cliente.post(url)
        self.assertEqual(Usuario.objects.get(pk=self.tc.pk).estado, EstadoCuenta.INACTIVA)
        self.assertFalse(DispositivoCampo.objects.filter(estado=EstadoDispositivo.ACTIVO).exists())
        self.assertEqual(cliente.post(url).status_code, 403)                     # C16: ya no está EN_BAJA
