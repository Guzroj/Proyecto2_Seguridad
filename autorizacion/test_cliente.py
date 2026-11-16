"""
Paso 13 · PEP y cliente del motor (lado Django).

Un transporte HTTP falso entrega cada petición al motor real (reglas.decidir),
así se prueba el mismo camino que en producción sin levantar el servicio.
"""

import unittest

try:
    import django  # noqa: F401
except ImportError:  # imagen del autorizador: solo corre las pruebas puras del motor
    raise unittest.SkipTest("Django no está instalado en esta imagen")

import json
import os
from types import SimpleNamespace
from unittest import mock

import httpx
from django.test import RequestFactory, TestCase, override_settings

from autorizacion import cliente, matriz, pep, reglas
from cuentas import services
from cuentas.models import EstadoCuenta
from cuentas.tests import crear_cuenta
from nucleo import ganchos
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, FueraDeAlcance, ReautenticacionRequerida

LOCAL = {"AUTORIZADOR_URL": "http://autorizador:8080", "AUTORIZADOR_AUTENTICACION": "ninguna"}


class MotorFalso:
    """Transporte que responde como el servicio autorizador y guarda lo recibido."""

    def __init__(self, respuesta=None):
        self.recibidas = []
        self.respuesta = respuesta  # función(request) -> httpx.Response, para simular fallas

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.recibidas.append(request)
        if self.respuesta:
            return self.respuesta(request)
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, text="token-oidc")
        return httpx.Response(200, json=reglas.decidir(json.loads(request.content)).como_dict())

    def instalar(self):
        return mock.patch.object(cliente, "_http", lambda: httpx.Client(transport=httpx.MockTransport(self)))

    @property
    def cuerpo(self) -> dict:
        return json.loads(self.recibidas[-1].content)


def con_rol(username, *roles_usuario, estado=EstadoCuenta.ACTIVA):
    u = crear_cuenta(username, estado)
    for r in roles_usuario:
        services.agregar_rol(u, r)
    return u


@override_settings(DEBUG=True)
@mock.patch.dict(os.environ, LOCAL)
class ClienteTests(TestCase):
    def setUp(self):
        self.gc = con_rol("gerente", "GC")
        self.tc = con_rol("tecnica", "TC")
        self.motor = MotorFalso()

    def test_concede_y_niega_segun_la_matriz(self):
        with self.motor.instalar():
            self.assertIsNone(cliente.autorizar(self.gc, Accion.ALERTA_LEER))
            with self.assertRaises(AccesoDenegado):
                cliente.autorizar(self.tc, Accion.ALERTA_LEER)

    def test_o9_en_pantalla_se_niega_a_tc_vf_ac_y_pf(self):
        with self.motor.instalar():
            for rol in ("TC", "VF", "AC", "PF"):
                with self.subTest(rol=rol), self.assertRaises(AccesoDenegado):
                    cliente.autorizar(con_rol(f"sujeto_{rol}", rol), Accion.ALERTA_LEER)

    def test_traduce_404_y_reto(self):
        with self.motor.instalar():
            with mock.patch.dict(reglas.CONDICIONES, C21=lambda s, r: reglas.fuera_de_alcance("C21")):
                with self.assertRaises(FueraDeAlcance):
                    cliente.autorizar(self.tc, Accion.CUENTA_LEER)
            with mock.patch.dict(reglas.CONDICIONES, C21=lambda s, r: reglas.reautenticar("C21")):
                with self.assertRaises(ReautenticacionRequerida):
                    cliente.autorizar(self.tc, Accion.FACTOR_REGISTRAR)

    def test_atributos_desde_la_base_sin_datos_personales(self):
        self.gc.first_name, self.gc.email = "Gabriela", "gabi@example.com"
        self.gc.save(update_fields=["first_name", "email"])
        with self.motor.instalar():
            cliente.autorizar(self.gc, Accion.ALERTA_LEER, self.tc, {"tecnico": self.tc, "n": 3})
        cuerpo = self.motor.cuerpo
        self.assertEqual(cuerpo["sujeto"], {"id": self.gc.pk, "estado": "ACTIVA", "roles": ["GC"], "mfa_edad": None})
        tecnica = {"tipo": "usuario", "id": self.tc.pk, "estado": "ACTIVA", "roles": ["TC"]}
        self.assertEqual(cuerpo["recurso"], tecnica)
        self.assertEqual(cuerpo["contexto"], {"tecnico": tecnica, "n": 3})
        texto = self.motor.recibidas[-1].content.decode()
        for dato in ("gerente", "Gabriela", "gabi@example.com", "password"):
            self.assertNotIn(dato, texto)

    def test_los_roles_se_leen_en_cada_decision(self):
        with self.motor.instalar():
            cliente.autorizar(self.gc, Accion.ALERTA_LEER)
            services.terminar_rol(self.gc, "GC")
            with self.assertRaises(AccesoDenegado):
                cliente.autorizar(self.gc, Accion.ALERTA_LEER)
        self.assertEqual(self.motor.cuerpo["sujeto"]["roles"], [])

    def test_cuenta_no_activa_se_niega_aunque_el_objeto_este_desactualizado(self):
        services.cambiar_estado(self.gc, EstadoCuenta.INACTIVA)
        self.gc.estado = EstadoCuenta.ACTIVA  # objeto viejo en memoria
        with self.motor.instalar(), self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.gc, Accion.ALERTA_LEER)
        self.assertEqual(self.motor.cuerpo["sujeto"]["estado"], "INACTIVA")

    def test_mfa_edad_sale_de_la_peticion_del_mismo_usuario(self):
        peticion = SimpleNamespace(user=self.gc, session={})
        with mock.patch("cuentas.mfa.edad_mfa", return_value=42.0):
            marca = pep.peticion_actual.set(peticion)
            try:
                self.assertEqual(pep.atributos_sujeto(self.gc)["mfa_edad"], 42.0)
                self.assertIsNone(pep.atributos_sujeto(self.tc)["mfa_edad"])  # otra persona
            finally:
                pep.peticion_actual.reset(marca)
        self.assertIsNone(pep.atributos_sujeto(self.gc)["mfa_edad"])  # fuera de una petición

    def test_middleware_deja_y_quita_la_peticion(self):
        vista = mock.Mock(side_effect=lambda r: pep.peticion_actual.get())
        peticion = RequestFactory().get("/")
        self.assertIs(pep.PeticionActualMiddleware(vista)(peticion), peticion)
        self.assertIsNone(pep.peticion_actual.get())

    def test_conectado_como_gancho(self):
        ganchos_reales = {"autorizar": "autorizacion.cliente.autorizar", "usuarios_con_rol": "cuentas.roles.usuarios_con_rol"}
        with override_settings(VERDEAZUL_GANCHOS=ganchos_reales), self.motor.instalar():
            ganchos.autorizar(self.gc, Accion.ALERTA_LEER)
            with self.assertRaises(AccesoDenegado):
                ganchos.autorizar(self.tc, Accion.ALERTA_LEER)


@override_settings(DEBUG=True)
@mock.patch.dict(os.environ, LOCAL)
class FalloCerradoTests(TestCase):
    """RG5: solo se concede con un 200 cuyo resultado es exactamente CONCEDE."""

    def setUp(self):
        self.gc = con_rol("gerente", "GC")

    def niega_con(self, respuesta):
        with MotorFalso(respuesta).instalar(), self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.gc, Accion.ALERTA_LEER)

    def test_respuestas_raras_niegan(self):
        concede = {"resultado": "CONCEDE"}
        casos = {
            "500": lambda r: httpx.Response(500, json=concede),
            "201": lambda r: httpx.Response(201, json=concede),
            "302": lambda r: httpx.Response(302, headers={"location": "http://otro/"}),
            "no json": lambda r: httpx.Response(200, text="CONCEDE"),
            "lista": lambda r: httpx.Response(200, json=["CONCEDE"]),
            "minúsculas": lambda r: httpx.Response(200, json={"resultado": "concede"}),
            "sin resultado": lambda r: httpx.Response(200, json={}),
            "otro valor": lambda r: httpx.Response(200, json={"resultado": "SI"}),
        }
        for nombre, respuesta in casos.items():
            with self.subTest(caso=nombre):
                self.niega_con(respuesta)

    def test_motor_caido_o_lento(self):
        for error in (httpx.ConnectError("caído"), httpx.ReadTimeout("lento")):
            with self.subTest(error=type(error).__name__):
                def falla(request, error=error):
                    raise error
                self.niega_con(falla)

    def test_sin_url(self):
        with mock.patch.dict(os.environ, {"AUTORIZADOR_URL": ""}), self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.gc, Accion.ALERTA_LEER)

    @override_settings(DEBUG=False)
    def test_sin_autenticacion_fuera_de_desarrollo(self):
        motor = MotorFalso()
        with motor.instalar(), self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.gc, Accion.ALERTA_LEER)
        self.assertEqual(motor.recibidas, [])  # ni siquiera se pregunta

    def test_modo_de_autenticacion_desconocido(self):
        with mock.patch.dict(os.environ, {"AUTORIZADOR_AUTENTICACION": "basica"}), self.assertRaises(AccesoDenegado):
            with MotorFalso().instalar():
                cliente.autorizar(self.gc, Accion.ALERTA_LEER)

    @override_settings(DEBUG=False)
    def test_oidc_manda_el_token_con_la_url_como_audiencia(self):
        motor = MotorFalso()
        with mock.patch.dict(os.environ, {"AUTORIZADOR_AUTENTICACION": "oidc"}), motor.instalar():
            cliente.autorizar(self.gc, Accion.ALERTA_LEER)
        token, decision = motor.recibidas
        self.assertEqual(token.url.params["audience"], "http://autorizador:8080")
        self.assertEqual(token.headers["Metadata-Flavor"], "Google")
        self.assertEqual(decision.headers["Authorization"], "Bearer token-oidc")

    @override_settings(DEBUG=False)
    def test_sin_token_oidc_se_niega(self):
        def sin_metadata(request):
            if request.url.host == "metadata.google.internal":
                return httpx.Response(404)
            return httpx.Response(200, json={"resultado": "CONCEDE"})

        with mock.patch.dict(os.environ, {"AUTORIZADOR_AUTENTICACION": "oidc"}):
            self.niega_con(sin_metadata)


class VocabularioTests(TestCase):
    def test_cada_accion_del_vocabulario_tiene_celda_o_es_de_un_proceso(self):
        acciones = {v for k, v in vars(Accion).items() if k.isupper()}
        sin_celda = acciones - set(matriz.ACCIONES) - matriz.ACCIONES_DE_PROCESO
        self.assertEqual(sin_celda, set())
        self.assertEqual(set(matriz.ACCIONES) - acciones, set())
