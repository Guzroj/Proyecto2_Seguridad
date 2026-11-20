"""
Servicio HTTP del motor (FastAPI). Necesita fastapi, que solo está en
la imagen del autorizador; en la de Django estas pruebas se saltan. Se corren con:

    docker compose exec autorizador python -m unittest autorizacion.test_app autorizacion.test_reglas
"""

import unittest

try:
    from fastapi.testclient import TestClient

    from autorizacion.app import app
except ImportError:  # imagen de Django
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi no está instalado en esta imagen")
class AppTests(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(app)

    def pedir(self, **cambios):
        cuerpo = {"sujeto": {"id": 7, "estado": "ACTIVA", "roles": ["GC"], "mfa_edad": None},
                  "accion": "alerta:leer", "recurso": None, "contexto": {}}
        cuerpo.update(cambios)
        return self.cliente.post("/autorizar", json=cuerpo)

    def test_healthz(self):
        self.assertEqual(self.cliente.get("/healthz").json(), {"ok": True})

    def test_decide_con_la_matriz(self):
        concede = self.pedir().json()
        self.assertEqual((concede["resultado"], concede["rol"]), ("CONCEDE", "GC"))
        niega = self.pedir(sujeto={"id": 7, "estado": "ACTIVA", "roles": ["TC"]}).json()
        self.assertEqual((niega["resultado"], niega["regla"]), ("NIEGA", "RG1"))

    def test_hbac_viaja_en_la_solicitud_y_las_alertas_en_la_respuesta(self):
        from autorizacion.test_reglas import HBAC_NEUTRO

        domingo = {**HBAC_NEUTRO, "hora": "2026-11-15T09:00:00-06:00"}
        d = self.pedir(accion="finca:leer", hbac=domingo).json()
        self.assertEqual((d["resultado"], d["marca_agua"]), ("CONCEDE", False))
        self.assertEqual([a["control"] for a in d["alertas"]], ["H2"])
        self.assertEqual(self.pedir(accion="finca:leer", hbac=[]).status_code, 422)

    def test_solicitudes_mal_formadas_dan_422(self):
        malas = [
            {"sujeto": {"id": "7", "estado": "ACTIVA", "roles": []}},          # id como texto (estricto)
            {"sujeto": {"id": True, "estado": "ACTIVA", "roles": []}},
            {"sujeto": {"id": 7, "estado": "ACTIVA", "roles": [], "nombre": "Ana"}},  # campo extra
            {"accion": ""},
            {"accion": "x" * 101},
            {"otro": 1},
        ]
        for cambio in malas:
            with self.subTest(cambio=cambio):
                self.assertEqual(self.pedir(**cambio).status_code, 422)

    def test_sin_documentacion_expuesta(self):
        for ruta in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.cliente.get(ruta).status_code, 404)
