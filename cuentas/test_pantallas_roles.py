"""
Paso 18b · Pantallas de roles (O7b, C7) y ventanas de auditoría (O7a AU, C14),
de punta a punta con login, motor real y servicios. Incluye V10.
"""

from datetime import timedelta

from django.urls import reverse

from cuentas.models import EstadoCuenta, EstadoSolicitudRol, SolicitudRol, Usuario, VentanaAuditoria
from cuentas.selectores import hoy, roles_vigentes
from cuentas.test_gestion import ultimo
from cuentas.test_pantallas import PantallasBase


class RolesTests(PantallasBase):
    def asignar(self, cliente, cuenta, rol):
        return cliente.post(reverse("gestion_rol_asignar", args=[cuenta.pk]), {"rol": rol})

    def test_ficha_con_roles_e_historial(self):
        respuesta = self.entrar(self.ac).get(reverse("gestion_cuenta", args=[self.tc.pk]))
        self.assertContains(respuesta, reverse("gestion_rol_retirar", args=[self.tc.pk, "TC"]))
        self.assertContains(respuesta, "Historial de roles")
        propia = self.entrar(self.tc).get(reverse("gestion_cuenta", args=[self.tc.pk]))
        self.assertNotContains(propia, "Historial de roles")                    # TC no tiene O7b
        self.assertNotContains(propia, "Asignar rol")

    def test_asignar_con_efecto_inmediato_y_v10(self):
        cliente = self.entrar(self.ac)
        self.assertRedirects(self.asignar(cliente, self.pf, "VF"), reverse("gestion_cuenta", args=[self.pf.pk]))
        self.assertEqual(roles_vigentes(self.pf), {"PF", "VF"})
        self.assertEqual(self.asignar(cliente, self.tc, "VF").status_code, 403)  # V10: TC ⊕ VF (C7 en el motor)
        self.assertEqual(self.asignar(cliente, self.ac, "PF").status_code, 403)  # a sí mismo
        self.assertEqual(self.asignar(cliente, self.pf, "XX").status_code, 400)

    def test_gc_queda_como_solicitud_y_otro_gc_la_autoriza(self):
        self.asignar(self.entrar(self.ac), self.pf, "GC")
        solicitud = SolicitudRol.objects.get(usuario=self.pf, rol="GC")
        self.assertEqual(roles_vigentes(self.pf), {"PF"})
        cliente = self.entrar(self.gc)
        lista = cliente.get(reverse("gestion_solicitudes_rol"))
        self.assertContains(lista, reverse("gestion_solicitud_autorizar", args=[solicitud.pk]))
        url = reverse("gestion_solicitud_autorizar", args=[solicitud.pk])
        self.assertContains(cliente.get(url), "admin_ac")                        # quién la pidió
        self.assertRedirects(cliente.post(url), reverse("gestion_solicitudes_rol"))
        self.assertEqual(roles_vigentes(self.pf), {"PF", "GC"})
        self.assertEqual(cliente.post(url).status_code, 403)                     # ya resuelta (C7)

    def test_rechazar_con_motivo(self):
        de_ac = self.entrar(self.ac)  # un solo ingreso: cada código TOTP sirve una vez
        self.assertEqual(self.asignar(de_ac, self.tc, "AE").status_code, 403)
        self.assertEqual(SolicitudRol.objects.count(), 0)                        # AE ⊕ TC: ni se pide
        self.asignar(de_ac, self.pf, "GC")
        solicitud = SolicitudRol.objects.get(usuario=self.pf)
        cliente = self.entrar(self.gc)
        cliente.post(reverse("gestion_solicitud_rechazar", args=[solicitud.pk]), {"motivo": "No corresponde"})
        solicitud.refresh_from_db()
        self.assertEqual((solicitud.estado, solicitud.motivo), (EstadoSolicitudRol.RECHAZADA, "No corresponde"))
        self.assertContains(cliente.get(reverse("gestion_solicitudes_rol")), "No corresponde")

    def test_solo_gc_resuelve_y_tc_no_ve_la_lista(self):
        cliente = self.entrar(self.ac)
        self.asignar(cliente, self.pf, "GC")
        solicitud = SolicitudRol.objects.get(usuario=self.pf)
        self.assertNotContains(cliente.get(reverse("gestion_solicitudes_rol")),
                               reverse("gestion_solicitud_autorizar", args=[solicitud.pk]))
        self.assertEqual(cliente.post(reverse("gestion_solicitud_autorizar", args=[solicitud.pk])).status_code, 403)
        self.assertEqual(ultimo("rol:autorizar")["resultado"], "DENEGADO")
        self.assertEqual(self.entrar(self.tc).get(reverse("gestion_solicitudes_rol")).status_code, 403)

    def test_retirar_con_confirmacion(self):
        cliente = self.entrar(self.ac)
        url = reverse("gestion_rol_retirar", args=[self.tc.pk, "TC"])
        self.assertContains(cliente.get(url), "Retirar")
        self.assertEqual(roles_vigentes(self.tc), {"TC"})                        # GET no cambia nada
        cliente.post(url)
        self.assertEqual(roles_vigentes(self.tc), set())
        self.assertEqual(cliente.post(url).status_code, 409)


class VentanasTests(PantallasBase):
    def crear_auditor(self, cliente=None):
        (cliente or self.entrar(self.ac)).post(reverse("gestion_cuenta_nueva"), {
            "username": "auditora", "rol": "AE", "inicio": hoy().isoformat(),
            "fin": (hoy() + timedelta(days=3)).isoformat()})
        return Usuario.objects.get(username="auditora")

    def test_gc_aprueba_y_la_cuenta_se_activa(self):
        auditora = self.crear_auditor()
        ventana = VentanaAuditoria.objects.get(auditor=auditora)
        cliente = self.entrar(self.gc)
        url = reverse("gestion_ventana_aprobar", args=[ventana.pk])
        self.assertContains(cliente.get(reverse("gestion_ventanas")), url)
        self.assertContains(cliente.get(url), "admin_ac")
        self.assertRedirects(cliente.post(url), reverse("gestion_ventanas"))
        auditora.refresh_from_db()
        self.assertEqual((auditora.estado, roles_vigentes(auditora)), (EstadoCuenta.ACTIVA, {"AE"}))

    def test_solo_gc_aprueba_y_tc_no_ve_ventanas(self):
        cliente = self.entrar(self.ac)
        ventana = VentanaAuditoria.objects.get(auditor=self.crear_auditor(cliente))
        self.assertNotContains(cliente.get(reverse("gestion_ventanas")),
                               reverse("gestion_ventana_aprobar", args=[ventana.pk]))
        self.assertEqual(cliente.post(reverse("gestion_ventana_aprobar", args=[ventana.pk])).status_code, 403)
        self.assertEqual(self.entrar(self.tc).get(reverse("gestion_ventanas")).context["pendientes"].count(), 0)
