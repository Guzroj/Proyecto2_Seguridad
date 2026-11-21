"""
Pantallas del flujo de negocio de punta a punta: login con segundo factor, motor de
autorización real, servicios y bitácora. Las vistas no deciden permisos: se
comprueba que el motor y los servicios los aplican y que los errores se ven como
403 / 404 / 409 aunque alguien llame la URL directamente.

Cubre en pantallas los casos V1, V2, V3, V4, V8, V15, V16 y V18 de la matriz (V5 está
en autorizacion.test_integracion_o4_o6, a nivel de servicio).
"""

from datetime import date
from unittest import mock

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse

from catalogo import pantallas as pantallas_catalogo
from catalogo import services as catalogo
from catalogo.models import ParcelaVersion
from cuentas.test_gestion import ultimo
from cuentas.test_pantallas import PantallasBase
from cuentas.test_rls_dominio import RLSMedicionesBase
from mediciones import pantallas, services
from mediciones.models import Enmienda, EstadoEnmienda, EstadoMedicion, Medicion, MotivoDevolucion

POLIGONO = '{"type": "Polygon", "coordinates": [[[-84.0, 10.0], [-84.0, 10.01], [-83.99, 10.01], [-84.0, 10.0]]]}'
DATOS = {"numero_arboles": 300, "dap_promedio_cm": "24.5", "altura_promedio_m": "17", "observaciones": ""}


class FlujoBase(PantallasBase):
    """Además de ac, gc, tc y pf (PantallasBase): verificadores, otro técnico, otro GC y otro PF."""

    def setUp(self):
        super().setUp()
        self.vf = self.crear("verificador", "VF", totp=True)
        self.vf2 = self.crear("verificador2", "VF", totp=True)
        self.tc2 = self.crear("tecnico2", "TC", totp=True)
        self.gc2 = self.crear("gerente2", "GC", totp=True)
        self.pf2 = self.crear("propietario2", "PF")

    def escenario(self):
        """Finca de pf con parcela, período abierto y tc asignado, creados con los servicios."""
        with self.settings(AUTORIZACION_PROVISIONAL_PERMISIVA=True, VERDEAZUL_GANCHOS={
                "usuarios_con_rol": "cuentas.roles.usuarios_con_rol", "registrar_evento": "cuentas.test_gestion.registrar"}):
            self.finca = catalogo.crear_finca(self.ac, "La Esperanza", "San Carlos", self.pf)
            self.parcela = catalogo.crear_parcela(self.ac, self.finca, codigo="P1", area_ha="2.5",
                                                  geometria={"type": "Polygon", "coordinates": [[[-84.0, 10.0], [-84.0, 10.01], [-83.99, 10.01], [-84.0, 10.0]]]})
            self.periodo = services.abrir_periodo(self.gc, "2026-II", date(2026, 7, 1), date(2026, 12, 31))
            services.crear_asignacion(self.ac, self.tc, self.finca, self.periodo)

    def elegir(self, verificador):
        """El verificador se escoge al azar (C2): en la prueba se fija cuál."""
        return mock.patch("mediciones.services._azar.choice",
                          side_effect=lambda candidatos: next(u for u in candidatos if u == verificador))


class MenuTests(FlujoBase):
    def test_menu_segun_la_celda_l_de_cada_rol(self):
        menu_tc = self.entrar(self.tc).get(reverse("inicio"))
        for nombre in ("catalogo_fincas", "mediciones_periodos", "mediciones_asignaciones",
                       "mediciones_mediciones", "mediciones_enmiendas"):
            self.assertContains(menu_tc, reverse(nombre))
        menu_pf = self.entrar(self.pf).get(reverse("inicio"))
        self.assertContains(menu_pf, reverse("mediciones_mediciones"))
        for nombre in ("mediciones_periodos", "mediciones_asignaciones", "mediciones_enmiendas"):  # PF: "—"
            self.assertNotContains(menu_pf, reverse(nombre))


class CatalogoTests(FlujoBase):
    def test_ac_crea_finca_parcela_y_datos_del_propietario(self):
        cliente = self.entrar(self.ac)
        r = cliente.post(reverse("catalogo_finca_nueva"),
                         {"nombre": "El Roble", "ubicacion": "Sarapiquí", "propietario": self.pf.pk})
        self.assertEqual(r.status_code, 302)
        finca = self.pf.fincas.get(nombre="El Roble")
        r = cliente.post(reverse("catalogo_parcela_nueva", args=[finca.pk]),
                         {"codigo": "R1", "area_ha": "3", "geometria": POLIGONO})
        self.assertEqual(r.status_code, 302)
        r = cliente.post(reverse("catalogo_propietario_datos", args=[finca.pk]),
                         {"nombre_completo": "Patricia Fallas", "identificacion": "1-1111-1111"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ultimo("propietario:crear_datos")["resultado"], "OK")  # CR la primera vez
        self.assertContains(cliente.get(reverse("catalogo_finca", args=[finca.pk])), "Patricia Fallas")

    def test_v18_nueva_geometria_crea_version_y_la_anterior_no_cambia(self):
        self.escenario()
        anterior = ParcelaVersion.objects.get(parcela=self.parcela)
        r = self.entrar(self.ac).post(reverse("catalogo_parcela_geometria", args=[self.parcela.pk]),
                                      {"area_ha": "2.75", "geometria": POLIGONO})
        self.assertEqual(r.status_code, 302)
        anterior.refresh_from_db()
        self.assertIsNotNone(anterior.vigente_hasta)
        self.assertEqual(self.parcela.versiones.count(), 2)

    def test_geometria_por_vertices_en_pantalla(self):
        """El AC escribe vértices «latitud, longitud»; la nueva versión parte de los vigentes."""
        self.escenario()
        cliente = self.entrar(self.ac)
        r = cliente.post(reverse("catalogo_parcela_nueva", args=[self.finca.pk]),
                         {"codigo": "V1", "area_ha": "1",
                          "geometria": "10.100, -84.000\n10.110, -84.000\n10.110, -83.990\n10.100, -83.990"})
        self.assertEqual(r.status_code, 302)
        parcela = self.finca.parcelas.get(codigo="V1")
        self.assertEqual(parcela.version_vigente().geometria["coordinates"][0][0], [-84.0, 10.1])
        ficha = cliente.get(reverse("catalogo_parcela", args=[parcela.pk]))
        self.assertContains(ficha, "10.1, -84.0")                       # vértices legibles, no JSON
        formulario = cliente.get(reverse("catalogo_parcela_geometria", args=[parcela.pk]))
        self.assertContains(formulario, "10.11, -83.99")                 # viene con los vigentes
        r = cliente.post(reverse("catalogo_parcela_nueva", args=[self.finca.pk]),   # C20 sigue igual
                         {"codigo": "V2", "area_ha": "1",
                          "geometria": "10.105, -84.000\n10.115, -84.000\n10.115, -83.990"})
        self.assertContains(r, "C20", status_code=400)

    def test_tc_no_crea_fincas_aunque_llame_la_url(self):
        r = self.entrar(self.tc).post(reverse("catalogo_finca_nueva"),
                                      {"nombre": "X", "ubicacion": "Y", "propietario": self.pf.pk})
        self.assertEqual(r.status_code, 403)  # el formulario se ve, pero el servidor niega (RG1)

    def test_v1_tc_no_ve_una_finca_no_asignada_y_es_404_igual_que_inexistente(self):
        self.escenario()
        cliente = self.entrar(self.tc2)
        ajena = cliente.get(reverse("catalogo_finca", args=[self.finca.pk]))
        inexistente = cliente.get(reverse("catalogo_finca", args=[999999]))
        self.assertEqual((ajena.status_code, inexistente.status_code), (404, 404))
        self.assertEqual(ajena.content, inexistente.content)

    def test_pf_no_ve_los_datos_personales_de_otro_propietario(self):
        self.escenario()
        r = self.entrar(self.pf2).get(reverse("catalogo_finca", args=[self.finca.pk]))
        self.assertEqual(r.status_code, 404)  # C4: la finca ajena no existe para él


class FlujoMedicionTests(FlujoBase):
    def crear_y_enviar(self, verificador):
        cliente_tc = self.entrar(self.tc)
        r = cliente_tc.post(reverse("mediciones_medicion_nueva"),
                            {"parcela": self.parcela.pk, "periodo": self.periodo.pk, **DATOS})
        self.assertEqual(r.status_code, 302, r.content[:500])
        m = Medicion.objects.get(autor=self.tc)
        self.assertEqual(m.estado, EstadoMedicion.BORRADOR)
        with self.elegir(verificador):
            self.assertEqual(cliente_tc.post(reverse("mediciones_medicion_enviar", args=[m.pk])).status_code, 302)
        m.refresh_from_db()
        self.assertEqual((m.estado, m.verificador), (EstadoMedicion.ENVIADA, verificador))
        return cliente_tc, m

    def test_crear_enviar_aprobar_y_v2_ya_no_se_modifica(self):
        self.escenario()
        cliente_tc, m = self.crear_y_enviar(self.vf)
        cliente_vf = self.entrar(self.vf)
        self.assertContains(cliente_vf.get(reverse("mediciones_medicion", args=[m.pk])),
                            reverse("mediciones_medicion_aprobar", args=[m.pk]))
        self.assertEqual(cliente_vf.post(reverse("mediciones_medicion_aprobar", args=[m.pk])).status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.estado, EstadoMedicion.APROBADA)
        r = cliente_tc.post(reverse("mediciones_medicion_modificar", args=[m.pk]), DATOS)  # V2 / H4
        self.assertIn(r.status_code, (403, 409))
        self.assertEqual(Medicion.objects.get(pk=m.pk).numero_arboles, 300)

    def test_v4_otro_verificador_no_aprueba_ni_ve_la_medicion(self):
        self.escenario()
        _, m = self.crear_y_enviar(self.vf)
        cliente_vf2 = self.entrar(self.vf2)
        self.assertEqual(cliente_vf2.get(reverse("mediciones_medicion", args=[m.pk])).status_code, 404)
        self.assertEqual(cliente_vf2.post(reverse("mediciones_medicion_aprobar", args=[m.pk])).status_code, 404)
        self.assertEqual(Medicion.objects.get(pk=m.pk).estado, EstadoMedicion.ENVIADA)

    def test_v3_devolver_corregir_en_verificacion_y_reenviar_a_otro_verificador(self):
        self.escenario()
        cliente_tc, m = self.crear_y_enviar(self.vf)
        motivo = MotivoDevolucion.objects.get(codigo="gps_fuera")
        r = self.entrar(self.vf).post(reverse("mediciones_medicion_devolver", args=[m.pk]),
                                      {"motivo": motivo.pk, "comentario": "Revisar el punto 3"})
        self.assertEqual(r.status_code, 302)
        self.entrar(self.gc).post(reverse("mediciones_periodo_pasar", args=[self.periodo.pk]))
        r = cliente_tc.post(reverse("mediciones_medicion_modificar", args=[m.pk]), {**DATOS, "numero_arboles": 310})
        self.assertEqual(r.status_code, 302)  # V3: DEVUELTA se corrige aún en EN_VERIFICACION
        with self.elegir(self.vf2):
            cliente_tc.post(reverse("mediciones_medicion_enviar", args=[m.pk]))
        m.refresh_from_db()
        self.assertEqual((m.estado, m.verificador, m.numero_arboles), (EstadoMedicion.ENVIADA, self.vf2, 310))

    def test_v16_gc_no_cierra_con_mediciones_pendientes(self):
        self.escenario()
        self.crear_y_enviar(self.vf)
        cliente_gc = self.entrar(self.gc)
        cliente_gc.post(reverse("mediciones_periodo_pasar", args=[self.periodo.pk]))
        r = cliente_gc.post(reverse("mediciones_periodo_cerrar", args=[self.periodo.pk]))
        self.assertIn(r.status_code, (403, 409))

    def test_v15_enmienda_por_pantallas_y_v8_pf_ve_solo_lo_suyo_aprobado(self):
        self.escenario()
        cliente_tc, m = self.crear_y_enviar(self.vf)
        self.entrar(self.vf).post(reverse("mediciones_medicion_aprobar", args=[m.pk]))
        r = cliente_tc.post(reverse("mediciones_enmienda_solicitar", args=[m.pk]),
                            {"justificacion": "Error de conteo en el transecto 2", **DATOS, "numero_arboles": 280})
        self.assertEqual(r.status_code, 302)
        e = Enmienda.objects.get(medicion_original=m)
        cliente_gc2 = self.entrar(self.gc2)
        self.assertContains(cliente_gc2.get(reverse("mediciones_enmiendas")),
                            reverse("mediciones_enmienda_autorizar", args=[e.pk]))
        with self.elegir(self.vf2):
            self.assertEqual(cliente_gc2.post(reverse("mediciones_enmienda_autorizar", args=[e.pk])).status_code, 302)
        e.refresh_from_db()
        self.assertEqual(e.estado, EstadoEnmienda.AUTORIZADA)
        self.assertEqual(e.version.verificador, self.vf2)
        self.entrar(self.vf2).post(reverse("mediciones_medicion_aprobar", args=[e.version_id]))
        e.refresh_from_db()
        self.assertEqual(e.estado, EstadoEnmienda.APLICADA)
        self.assertEqual(Medicion.objects.get(pk=m.pk).numero_arboles, 300)  # la original no cambia

        lista_pf = self.entrar(self.pf).get(reverse("mediciones_mediciones"))  # C4: aprobadas de su finca
        self.assertEqual(lista_pf.status_code, 200)
        self.assertContains(lista_pf, str(m.pk)[:8])
        self.assertEqual(self.entrar(self.pf2).get(reverse("mediciones_medicion", args=[m.pk])).status_code, 404)  # V8


class PantallasBajoRLSTests(RLSMedicionesBase):
    """
    Las pantallas con el rol real de la aplicación (sin privilegios, RLS con FORCE).
    Bajo RLS un PF no lee períodos y un VF no lee la cuenta del técnico: si una vista
    hiciera JOIN a esas tablas, la fila desaparecería de la lista o la página fallaría.
    """

    def ver(self, vista, usuario, *args):
        request = RequestFactory().get("/")
        request.user, request.session = usuario, {}
        request._messages = FallbackStorage(request)
        with self.como(usuario):
            respuesta = vista(request, *args)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.content.decode()

    def test_lista_de_mediciones_no_pierde_filas_por_rls(self):
        corto = str(self.medicion.pk)[:8]
        for usuario in (self.pf, self.vf, self.tc, self.gc, self.ae):  # PF: aprobada propia; VF: la verificó
            with self.subTest(usuario=usuario.username):
                self.assertIn(corto, self.ver(pantallas.mediciones, usuario))
        self.assertNotIn(corto, self.ver(pantallas.mediciones, self.pf2))  # C4: no es suya

    def test_ficha_de_medicion_sin_periodo_ni_cuentas_ajenas(self):
        pagina_pf = self.ver(pantallas.medicion, self.pf, self.medicion.pk)   # PF no tiene O2 ni O7a ajeno
        self.assertIn(f"#{self.medicion.periodo_id}", pagina_pf)
        self.assertIn(f"#{self.tc.pk}", pagina_pf)
        pagina_vf = self.ver(pantallas.medicion, self.vf, self.medicion.pk)
        self.assertIn("(usted)", pagina_vf)                                 # el verificador es él
        self.assertIn(self.periodo.nombre, pagina_vf)                        # el VF sí lee períodos

    def test_listas_de_asignaciones_enmiendas_y_fincas(self):
        self.assertIn(self.finca.nombre, self.ver(pantallas.asignaciones, self.tc))
        self.assertIn("Conteo", self.ver(pantallas.enmiendas, self.gc))
        self.assertIn(self.finca.nombre, self.ver(pantallas_catalogo.fincas, self.pf))
        self.assertIn(self.parcela.codigo, self.ver(pantallas_catalogo.finca, self.tc, self.finca.pk))
