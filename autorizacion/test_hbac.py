"""
Paso 15 · Controles HBAC H1–H3 en el motor, con contadores armados a mano.
Python puro: corre en Django y en el contenedor del autorizador. Incluye V11, V12 y V13.
"""

import unittest

from autorizacion import reglas
from autorizacion.reglas import CONCEDE, NIEGA, REAUTENTICAR
from autorizacion.test_reglas import HBAC_NEUTRO

S = 7
MIERCOLES_10 = "2026-11-11T10:00:00-06:00"
FINCA_ASIGNADA = {"tipo": "finca", "id": 1, "estado": "ACTIVA", "propietario": 99,
                  "tecnicos_vigentes": [S], "verificadores": []}
VENTANA_AE = {"estado": "APROBADA", "inicio": "2026-11-01", "fin": "2026-11-20", "hoy": "2026-11-11",
              "aprobada_por_gc": True, "aprobada_por_distinto": True}


def decidir(accion, rol, *, recurso=None, contexto=None, mfa_edad=None, **hbac):
    sujeto = {"id": S, "estado": "ACTIVA", "roles": [rol], "mfa_edad": mfa_edad}
    if rol == "AE":
        sujeto["ventana"] = VENTANA_AE
    return reglas.decidir({"sujeto": sujeto, "accion": accion, "recurso": recurso, "contexto": contexto or {},
                           "hbac": {**HBAC_NEUTRO, **hbac}})


class Base(unittest.TestCase):
    def assertDecision(self, decision, resultado, regla=None):
        self.assertEqual(decision.resultado, resultado, decision.motivo)
        if regla:
            self.assertEqual(decision.regla, regla)

    def controles(self, decision):
        return [a["control"] for a in decision.alertas]


class H1Tests(Base):
    def test_sin_historial_el_umbral_es_25_incluida_la_peticion(self):
        self.assertDecision(decidir("finca:leer", "GC", h1_lecturas=24), CONCEDE)
        d = decidir("finca:leer", "GC", h1_lecturas=25)
        self.assertDecision(d, NIEGA, "H1")
        self.assertEqual(d.alertas, ({"control": "H1", "detalle": {"accion": "finca:leer", "n": 26,
                                                                 "umbral": 25, "media": 0.0}},))

    def test_v11_gc_lee_a_mas_del_triple_de_su_media(self):
        self.assertDecision(decidir("medicion:leer", "GC", h1_lecturas=59, h1_media_propia=20), CONCEDE)
        d = decidir("medicion:leer", "GC", h1_lecturas=60, h1_media_propia=20)
        self.assertDecision(d, NIEGA, "H1")
        self.assertEqual((d.alertas[0]["detalle"]["n"], d.alertas[0]["detalle"]["umbral"]), (61, 60))

    def test_con_menos_historial_usa_la_media_del_rol_que_concede(self):
        medias = {"GC": 12.0, "TC": 30.0}
        self.assertDecision(decidir("periodo:leer", "GC", h1_lecturas=500, h1_media_rol=medias), CONCEDE)  # O2: sin H1
        self.assertDecision(decidir("finca:leer", "GC", h1_lecturas=36, h1_media_rol=medias), NIEGA, "H1")
        self.assertDecision(decidir("finca:leer", "GC", h1_lecturas=35, h1_media_rol=medias), CONCEDE)
        self.assertDecision(decidir("finca:leer", "GC", h1_lecturas=26, h1_media_rol={"GC": None}), NIEGA, "H1")

    def test_cubre_o1a_o1b_y_o4_de_tc_vf_gc_ac(self):
        for accion, rol, recurso in (("finca:leer", "AC", None), ("propietario:leer", "AC", None),
                                     ("finca:leer", "TC", FINCA_ASIGNADA), ("medicion:leer", "GC", None)):
            with self.subTest(accion=accion, rol=rol):
                self.assertDecision(decidir(accion, rol, recurso=recurso, h1_lecturas=40), NIEGA, "H1")

    def test_pf_y_ae_no_cuentan_frecuencia(self):
        propia = {**FINCA_ASIGNADA, "propietario": S}
        self.assertDecision(decidir("finca:leer", "PF", recurso=propia, h1_lecturas=400), CONCEDE)
        self.assertDecision(decidir("finca:leer", "AE", h1_lecturas=400), CONCEDE)

    def test_suspendido_no_lee_o1_ni_o4_con_ningun_rol(self):
        propia = {**FINCA_ASIGNADA, "propietario": S}
        for accion, rol, recurso in (("finca:leer", "GC", None), ("medicion:leer", "GC", None),
                                     ("finca:leer", "PF", propia), ("propietario:leer", "AC", None),
                                     ("medicion:leer", "AE", None)):
            with self.subTest(accion=accion, rol=rol):
                d = decidir(accion, rol, recurso=recurso, h1_suspendido=True)
                self.assertDecision(d, NIEGA, "H1")
                self.assertEqual(d.alertas, ())  # la alerta ya existe
        sujeto = {"id": S, "estado": "ACTIVA", "roles": ["PF", "TC"], "mfa_edad": None}
        d = reglas.decidir({"sujeto": sujeto, "accion": "finca:leer", "recurso": propia, "contexto": {},
                            "hbac": {**HBAC_NEUTRO, "h1_suspendido": True}})
        self.assertDecision(d, NIEGA, "H1")                                   # TC+PF: tampoco como PF
        self.assertDecision(decidir("periodo:leer", "GC", h1_suspendido=True), CONCEDE)
        self.assertDecision(decidir("medicion:exportar", "AE", contexto={"registros": 1}, h1_suspendido=True),
                            CONCEDE)                                          # suspende lecturas, no X

    def test_tras_la_resolucion_la_primera_lectura_exige_mfa(self):
        self.assertDecision(decidir("finca:leer", "GC", h1_resuelta_hace=100), REAUTENTICAR, "H1")
        self.assertDecision(decidir("finca:leer", "GC", h1_resuelta_hace=100, mfa_edad=150), REAUTENTICAR, "H1")
        self.assertDecision(decidir("finca:leer", "GC", h1_resuelta_hace=100, mfa_edad=40), CONCEDE)
        self.assertDecision(decidir("finca:leer", "GC", h1_resuelta_hace=9000, mfa_edad=600), CONCEDE)

    def test_contadores_faltantes_o_raros_niegan(self):
        for clave, valor in (("h1_lecturas", None), ("h1_lecturas", -1), ("h1_lecturas", True),
                             ("h1_suspendido", None), ("h1_media_propia", "10"), ("h1_media_rol", [])):
            with self.subTest(clave=clave, valor=valor):
                self.assertDecision(decidir("finca:leer", "GC", **{clave: valor}), NIEGA, "RG5")
        sin_contadores = reglas.decidir({"sujeto": {"id": S, "estado": "ACTIVA", "roles": ["GC"]},
                                         "accion": "finca:leer", "recurso": None, "contexto": {}})
        self.assertDecision(sin_contadores, NIEGA, "RG5")
        self.assertDecision(reglas.decidir({"sujeto": {"id": S, "estado": "ACTIVA", "roles": ["GC"]},
                                            "accion": "finca:leer", "hbac": []}), NIEGA, "RG5")


class H2Tests(Base):
    def test_v12_tc_lee_su_finca_a_medianoche(self):
        d = decidir("finca:leer", "TC", recurso=FINCA_ASIGNADA, hora="2026-11-12T00:00:00-06:00")
        self.assertDecision(d, CONCEDE, "C6")
        self.assertEqual(d.alertas, ({"control": "H2", "detalle": {"accion": "finca:leer", "fuera_de_horario": True}},))

    def test_horario(self):
        casos = [("2026-11-11T04:59:59-06:00", True), ("2026-11-11T05:00:00-06:00", False),
                 ("2026-11-11T23:59:00-06:00", False), ("2026-11-14T13:00:00-06:00", True),  # sábado
                 ("2026-11-15T09:00:00-06:00", True),                                     # domingo
                 ("2026-11-11T10:30:00+00:00", True),     # 04:30 en Costa Rica
                 ("2026-11-14T05:30:00+00:00", False)]    # viernes 23:30 en Costa Rica
        for hora, fuera in casos:
            with self.subTest(hora=hora):
                d = decidir("medicion:leer", "GC", hora=hora)
                self.assertDecision(d, CONCEDE)
                self.assertEqual(self.controles(d), ["H2"] if fuera else [])

    def test_solo_en_celdas_con_marca(self):
        domingo = "2026-11-15T09:00:00-06:00"
        self.assertEqual(decidir("certificado:leer", "GC", hora=domingo).alertas[0]["control"], "H2")  # O6 ‡
        self.assertEqual(decidir("periodo:leer", "GC", hora=domingo).alertas, ())
        self.assertEqual(decidir("alerta:leer", "GC", hora=domingo).alertas, ())

    def test_sin_hora_valida_niega(self):
        for hora in (None, "ayer", "2026-11-11T10:00:00", 5):
            with self.subTest(hora=hora):
                self.assertDecision(decidir("finca:leer", "GC", hora=hora), NIEGA, "RG5")


class H3Tests(Base):
    def exportar(self, registros, **hbac):
        return decidir("medicion:exportar", "AE", contexto={"registros": registros}, **hbac)

    def test_v13_ae_exporta_2000_con_segundo_factor_reciente(self):
        d = self.exportar(2000, mfa_edad=30)
        self.assertDecision(d, CONCEDE, "C5")
        self.assertTrue(d.marca_agua)
        self.assertEqual(d.alertas, ())

    def test_mas_de_500_en_24_h_exige_mfa(self):
        self.assertDecision(self.exportar(2000), REAUTENTICAR, "H3")
        self.assertDecision(self.exportar(2000, mfa_edad=301), REAUTENTICAR, "H3")
        self.assertDecision(self.exportar(50, h3_exportados=450), CONCEDE)       # N = 500
        self.assertDecision(self.exportar(51, h3_exportados=450), REAUTENTICAR, "H3")

    def test_fuera_de_horario_concede_con_reautenticacion_y_alerta(self):
        sabado = "2026-11-14T10:00:00-06:00"
        self.assertDecision(self.exportar(800, hora=sabado), REAUTENTICAR, "H3")
        d = self.exportar(800, hora=sabado, mfa_edad=10)
        self.assertDecision(d, CONCEDE)
        self.assertEqual(d.alertas, ({"control": "H3", "detalle": {"accion": "medicion:exportar", "registros": 800,
                                                                 "fuera_de_horario": True}},))
        self.assertEqual(self.exportar(10, hora=sabado).alertas, ())  # pequeña: sin alerta

    def test_toda_exportacion_lleva_marca_de_agua(self):
        self.assertTrue(self.exportar(1).marca_agua)
        self.assertFalse(decidir("medicion:leer", "AE").marca_agua)

    def test_sin_registros_niega(self):
        for contexto in ({}, {"registros": -3}, {"registros": "10"}, {"registros": True}):
            with self.subTest(contexto=contexto):
                self.assertDecision(decidir("medicion:exportar", "AE", contexto=contexto), NIEGA, "RG5")


class ContratoTests(Base):
    def test_la_decision_lleva_alertas_y_marca_de_agua(self):
        datos = decidir("finca:leer", "GC", hora="2026-11-15T09:00:00-06:00").como_dict()
        self.assertEqual(datos["alertas"], [{"control": "H2", "detalle": {"accion": "finca:leer",
                                                                         "fuera_de_horario": True}}])
        self.assertFalse(datos["marca_agua"])
