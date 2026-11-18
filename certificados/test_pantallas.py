"""
Pantallas de Persona 4 de punta a punta: ingreso con segundo factor, motor de autorización
real, servicios, bitácora y emisión reales. Las vistas no deciden permisos: se comprueba que
el servidor rechaza aunque alguien llame la URL directamente (403 / 404 / 405).
"""

import json
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from bitacora.models import Evento
from catalogo import services as catalogo
from certificados import firma
from certificados.models import Certificado
from cuentas import services as cuentas
from cuentas.selectores import hoy
from cuentas.test_mfa import SECRETO
from cuentas.tests import CLAVE
from exportaciones.test_exportaciones import GANCHOS_REALES, leer_csv
from mediciones.models import Medicion
from mediciones.test_pantallas import DATOS, FlujoBase
from nucleo.acciones import Accion

PERMISIVO = {"AUTORIZACION_PROVISIONAL_PERMISIVA": True,
             "VERDEAZUL_GANCHOS": {"usuarios_con_rol": "cuentas.roles.usuarios_con_rol",
                                   "registrar_evento": "bitacora.servicios.registrar_evento"}}


@override_settings(VERDEAZUL_GANCHOS=GANCHOS_REALES)
class PantallasP4Base(FlujoBase):
    def setUp(self):
        super().setUp()
        self.escenario()
        with self.settings(**PERMISIVO):
            catalogo.registrar_datos_propietario(self.ac, self.pf, nombre_completo="Patricia Fallas Mora",
                                                 identificacion="1-1111-1111")
            self.ae = cuentas.crear_cuenta(self.ac, username="auditora", rol="AE", inicio=hoy(),
                                           fin=hoy() + timedelta(days=5))
            cuentas.aprobar_ventana(self.gc, self.ae.ventana_auditoria)
        self.ae.refresh_from_db()
        self.ae.set_password(CLAVE)
        self.ae.save(update_fields=["password"])
        from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
        from allauth.mfa.totp.internal.auth import TOTP

        TOTP.activate(self.ae, SECRETO)
        RecoveryCodes.activate(self.ae)

    def entrar(self, usuario):
        """Un cliente por usuario: allauth no acepta el mismo código TOTP dos veces (como en producción)."""
        clientes = self.__dict__.setdefault("_clientes", {})
        if usuario.pk not in clientes:
            clientes[usuario.pk] = super().entrar(usuario)
        return clientes[usuario.pk]

    def aprobar_por_pantalla(self) -> Medicion:
        cliente_tc = self.entrar(self.tc)
        cliente_tc.post(reverse("mediciones_medicion_nueva"), {"parcela": self.parcela.pk, "periodo": self.periodo.pk,
                                                                **DATOS})
        m = Medicion.objects.get(autor=self.tc)
        with self.elegir(self.vf):
            cliente_tc.post(reverse("mediciones_medicion_enviar", args=[m.pk]))
        self.assertEqual(self.entrar(self.vf).post(reverse("mediciones_medicion_aprobar", args=[m.pk])).status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.estado, "APROBADA")
        return m


class CertificadosPantallaTests(PantallasP4Base):
    def test_aprobar_por_pantalla_emite_y_el_pf_ve_y_descarga_el_suyo(self):
        m = self.aprobar_por_pantalla()
        c = Certificado.objects.get(medicion=m, estado="EMITIDO")
        pf = self.entrar(self.pf)
        self.assertContains(pf.get(reverse("certificados_certificados")), str(c.pk)[:8])
        ficha = pf.get(reverse("certificados_certificado", args=[c.pk]))
        self.assertContains(ficha, "Verificado")
        self.assertContains(ficha, "Patricia Fallas Mora")
        descarga = pf.get(reverse("certificados_descargar", args=[c.pk]))
        self.assertEqual(descarga["Content-Type"], "application/json; charset=utf-8")
        from certificados import formato

        self.assertTrue(formato.verificar(descarga.content, firma.huellas_confiables())["valido"])
        self.assertEqual(descarga["Cache-Control"], "no-store")
        # La lectura concedida de O6 queda en la bitácora (RG4).
        self.assertTrue(Evento.objects.filter(accion=Accion.CERTIFICADO_LEER, actor=self.pf,
                                              recurso_id=str(c.pk), resultado="CONCEDIDO").exists())

    def test_v8_otro_pf_recibe_404_igual_que_inexistente_y_queda_en_bitacora(self):
        c = Certificado.objects.get(medicion=self.aprobar_por_pantalla())
        cliente = self.entrar(self.pf2)
        ajeno = cliente.get(reverse("certificados_certificado", args=[c.pk]))
        inexistente = cliente.get(reverse("certificados_certificado", args=["00000000-0000-0000-0000-000000000000"]))
        self.assertEqual((ajeno.status_code, inexistente.status_code), (404, 404))
        self.assertEqual(ajeno.content, inexistente.content)
        self.assertEqual(cliente.get(reverse("certificados_descargar", args=[c.pk])).status_code, 404)
        self.assertNotContains(cliente.get(reverse("certificados_certificados")), str(c.pk)[:8])
        self.assertTrue(Evento.objects.filter(accion=Accion.CERTIFICADO_LEER, actor=self.pf2,
                                              resultado="DENEGADO").exists())

    def test_tc_vf_y_ac_no_tienen_o6_aunque_llamen_la_url(self):
        c = Certificado.objects.get(medicion=self.aprobar_por_pantalla())
        for usuario in (self.tc, self.vf, self.ac):
            with self.subTest(usuario=usuario.username):
                cliente = self.entrar(usuario)
                self.assertEqual(cliente.get(reverse("certificados_certificados")).status_code, 403)
                self.assertIn(cliente.get(reverse("certificados_certificado", args=[c.pk])).status_code, (403, 404))

    def test_gc_y_ae_ven_todos_y_el_menu_lo_refleja(self):
        c = Certificado.objects.get(medicion=self.aprobar_por_pantalla())
        for usuario in (self.gc, self.ae):
            with self.subTest(usuario=usuario.username):
                cliente = self.entrar(usuario)
                self.assertContains(cliente.get(reverse("certificados_certificados")), str(c.pk)[:8])
                self.assertEqual(cliente.get(reverse("certificados_certificado", args=[c.pk])).status_code, 200)
        self.assertNotContains(self.entrar(self.tc).get(reverse("inicio")), reverse("certificados_certificados"))


class VerificacionPublicaTests(PantallasP4Base):
    def setUp(self):
        super().setUp()
        self.c = Certificado.objects.get(medicion=self.aprobar_por_pantalla())

    def test_cualquiera_verifica_sin_cuenta(self):
        anonimo = Client()
        self.assertEqual(anonimo.get(reverse("certificados_verificar")).status_code, 200)
        archivo = SimpleUploadedFile("certificado.json", self.c.archivo().encode(), content_type="application/json")
        r = anonimo.post(reverse("certificados_verificar"), {"archivo": archivo})
        self.assertContains(r, "VÁLIDO")
        self.assertContains(r, "Estado en el registro de Verdeazul:</strong> EMITIDO")
        evento = Evento.objects.filter(accion=Accion.CERTIFICADO_VERIFICAR).latest("secuencia")
        self.assertEqual((evento.actor_id, evento.despues["valido"]), (None, True))

    def test_un_certificado_alterado_falla(self):
        documento = self.c.documento()
        documento["contenido"]["toneladas_co2"] = "999999.0000"
        r = Client().post(reverse("certificados_verificar"), {"texto": json.dumps(documento)})
        self.assertEqual(r.status_code, 422)
        self.assertContains(r, "NO VÁLIDO", status_code=422)
        self.assertContains(r, "alterado", status_code=422)
        self.assertNotContains(r, "Estado en el registro", status_code=422)

    def test_limite_de_tasa_por_ip(self):
        from django.core.cache import cache

        cache.clear()
        documento = {"texto": self.c.archivo()}
        url = reverse("certificados_verificar")
        anonimo = Client(HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.7")
        with override_settings(CERTIFICADO_VERIFICACION_LIMITES={"ip_minuto": (2, 60), "instancia_minuto": (100, 60)}):
            self.assertEqual([anonimo.post(url, documento).status_code for _ in range(2)], [200, 200])
            bloqueada = anonimo.post(url, documento)
            self.assertEqual(bloqueada.status_code, 429)
            self.assertEqual(bloqueada["Retry-After"], "60")
            self.assertEqual(anonimo.post(url, documento).status_code, 429)
            # La IP es la que agrega Cloud Run (la última): otro cliente no queda bloqueado.
            otro = Client(HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.8")
            self.assertEqual(otro.post(url, documento).status_code, 200)
        avisos = Evento.objects.filter(accion=Accion.CERTIFICADO_VERIFICAR, resultado="DENEGADO")
        self.assertEqual(avisos.count(), 1)  # un aviso por ventana, no uno por intento
        self.assertEqual(avisos.get().origen, "198.51.100.7")
        cache.clear()

    def test_clave_publica_publicada(self):
        datos = Client().get(reverse("certificados_clave_publica")).json()
        self.assertEqual(datos["algoritmo"], "Ed25519")
        self.assertIn(self.c.huella_clave, datos["huellas_confiables"])
        self.assertEqual(datos["clave_actual"]["huella"], self.c.huella_clave)
        self.assertNotIn("privada", json.dumps(datos).lower().replace("clave_publica", ""))


class BitacoraPantallaTests(PantallasP4Base):
    def setUp(self):
        super().setUp()
        self.aprobar_por_pantalla()

    def test_gc_y_ae_leen_la_bitacora_y_verifican_la_cadena(self):
        for usuario in (self.gc, self.ae):
            with self.subTest(usuario=usuario.username):
                cliente = self.entrar(usuario)
                self.assertContains(cliente.get(reverse("bitacora_eventos")), "medicion:aprobar")
                self.assertContains(cliente.get(reverse("bitacora_eventos"), {"accion": "certificado"}),
                                    "certificado:emitir")
                self.assertContains(cliente.get(reverse("bitacora_verificar")), "Íntegra")
                evento = Evento.objects.get(accion=Accion.CERTIFICADO_EMITIR)
                self.assertContains(cliente.get(reverse("bitacora_evento", args=[evento.pk])), evento.hash)

    def test_nadie_mas_la_lee_ni_nadie_la_modifica(self):
        for usuario in (self.tc, self.vf, self.ac, self.pf):
            with self.subTest(usuario=usuario.username):
                cliente = self.entrar(usuario)
                self.assertEqual(cliente.get(reverse("bitacora_eventos")).status_code, 403)
                self.assertEqual(cliente.get(reverse("bitacora_verificar")).status_code, 403)
        gc = self.entrar(self.gc)
        evento = Evento.objects.order_by("secuencia").first()
        self.assertEqual(gc.post(reverse("bitacora_evento", args=[evento.pk])).status_code, 405)
        self.assertEqual(gc.post(reverse("bitacora_eventos")).status_code, 405)


class ExportacionPantallaTests(PantallasP4Base):
    def setUp(self):
        super().setUp()
        self.aprobar_por_pantalla()

    def entrar_ae(self):
        return self.entrar(self.ae)

    def test_ae_exporta_por_post_y_el_archivo_lleva_la_marca(self):
        cliente = self.entrar_ae()
        url = reverse("exportaciones_exportar", args=["certificados"])
        self.assertContains(cliente.get(reverse("exportaciones_lista")), url)
        antes = Evento.objects.filter(accion=Accion.CERTIFICADO_EXPORTAR).count()
        self.assertEqual(cliente.get(url).status_code, 200)  # GET solo confirma: no exporta
        self.assertEqual(Evento.objects.filter(accion=Accion.CERTIFICADO_EXPORTAR).count(), antes)
        r = cliente.post(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        filas = leer_csv(r.content)
        self.assertIn(f"Exportado por auditora (cuenta {self.ae.pk})", filas[0][0])
        self.assertEqual(int(r["X-Exportacion-Registros"]), Certificado.objects.count())
        self.assertTrue(Evento.objects.filter(accion=Accion.CERTIFICADO_EXPORTAR, actor=self.ae, resultado="OK").exists())

    def test_otros_roles_reciben_403_aunque_llamen_la_url(self):
        for usuario in (self.gc, self.pf, self.tc):
            with self.subTest(usuario=usuario.username):
                r = self.entrar(usuario).post(reverse("exportaciones_exportar", args=["bitacora"]))
                self.assertEqual(r.status_code, 403)
        self.assertEqual(Evento.objects.filter(accion=Accion.BITACORA_EXPORTAR, resultado="DENEGADO").count(), 3)
        self.assertEqual(self.entrar_ae().post(reverse("exportaciones_exportar", args=["no-existe"])).status_code, 404)
