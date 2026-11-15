"""
Paso 14a · Condiciones de O1–O3 (C4, C6, C8, C9, C13, C19, C20) con atributos
armados a mano. Python puro: corre en Django y en el contenedor del autorizador.
"""

import unittest

from autorizacion import reglas
from autorizacion.reglas import CONCEDE, FUERA_DE_ALCANCE, NIEGA

S = 7  # id del sujeto
OTRO = 99


def decidir(accion, rol, recurso=None, contexto=None, sujeto=S):
    return reglas.decidir({"sujeto": {"id": sujeto, "estado": "ACTIVA", "roles": [rol], "mfa_edad": None},
                           "accion": accion, "recurso": recurso, "contexto": contexto or {}})


def finca(propietario=OTRO, tecnicos=(), verificadores=(), estado="ACTIVA", tipo="finca"):
    return {"tipo": tipo, "id": 1, "estado": estado, "propietario": propietario,
            "tecnicos_vigentes": list(tecnicos), "verificadores": list(verificadores)}


class Base(unittest.TestCase):
    def assertDecision(self, decision, resultado, regla=None):
        self.assertEqual(decision.resultado, resultado, decision.motivo)
        if regla:
            self.assertEqual(decision.regla, regla)


class C4Tests(Base):
    def test_o1a_y_o1b_solo_lo_propio(self):
        for accion, recurso in (("finca:leer", finca(propietario=S)), ("parcela:leer", finca(propietario=S, tipo="parcela")),
                                ("propietario:leer", {"tipo": "datospropietario", "id": 1, "propietario": S})):
            with self.subTest(accion=accion):
                self.assertDecision(decidir(accion, "PF", recurso), CONCEDE, "C4")
                ajeno = {**recurso, "propietario": OTRO}
                self.assertDecision(decidir(accion, "PF", ajeno), FUERA_DE_ALCANCE, "C4")

    def test_o4_solo_aprobadas(self):
        medicion = {"tipo": "medicion", "id": "m", "propietario": S, "estado": "APROBADA"}
        self.assertDecision(decidir("medicion:leer", "PF", medicion), CONCEDE)
        for estado in ("BORRADOR", "ENVIADA", "DEVUELTA"):
            with self.subTest(estado=estado):
                self.assertDecision(decidir("medicion:leer", "PF", {**medicion, "estado": estado}), FUERA_DE_ALCANCE)

    def test_o6_emitidos_y_revocados(self):
        certificado = {"tipo": "certificado", "id": 1, "propietario": S}
        for estado, resultado in (("EMITIDO", CONCEDE), ("REVOCADO", CONCEDE), ("ANULADO", FUERA_DE_ALCANCE)):
            with self.subTest(estado=estado):
                self.assertDecision(decidir("certificado:leer", "PF", {**certificado, "estado": estado}), resultado)

    def test_sin_atributos_se_niega(self):
        self.assertDecision(decidir("finca:leer", "PF"), NIEGA, "RG5")
        self.assertDecision(decidir("finca:leer", "PF", {"tipo": "finca", "id": 1}), NIEGA, "RG5")
        self.assertDecision(decidir("certificado:leer", "PF", {"tipo": "certificado", "id": 1, "propietario": S}),
                            NIEGA, "RG5")

    def test_pf_no_tiene_o2_ni_o3(self):
        self.assertDecision(decidir("periodo:leer", "PF", {"tipo": "periodo", "id": 1}), NIEGA, "RG1")


class C6C8C10Tests(Base):
    def test_c6_tc_lee_la_finca_que_tiene_asignada(self):
        for tipo in ("finca", "parcela"):
            with self.subTest(tipo=tipo):
                d = decidir(f"{tipo}:leer", "TC", finca(tecnicos=[S], tipo=tipo))
                self.assertDecision(d, CONCEDE, "C6")
                self.assertEqual(d.hbac, ("H1", "H2"))
                self.assertDecision(decidir(f"{tipo}:leer", "TC", finca(tecnicos=[OTRO], tipo=tipo)),
                                    FUERA_DE_ALCANCE, "C6")  # V1

    def test_c6_no_da_datos_personales(self):
        self.assertDecision(decidir("propietario:leer", "TC", {"tipo": "datospropietario", "id": 1}), NIEGA, "RG1")

    def test_c8_tc_lee_sus_asignaciones(self):
        asignacion = {"tipo": "asignacion", "id": 1, "tecnico": S}
        self.assertDecision(decidir("asignacion:leer", "TC", asignacion), CONCEDE, "C8")
        self.assertDecision(decidir("asignacion:leer", "TC", {**asignacion, "tecnico": OTRO}), FUERA_DE_ALCANCE)


class C9Tests(Base):
    def test_o4_verificador_asignado_o_actor(self):
        medicion = {"tipo": "medicion", "id": "m", "verificador": S, "actores": []}
        self.assertDecision(decidir("medicion:leer", "VF", medicion), CONCEDE, "C9")
        self.assertDecision(decidir("medicion:leer", "VF", {**medicion, "verificador": OTRO, "actores": [S]}), CONCEDE)
        self.assertDecision(decidir("medicion:leer", "VF", {**medicion, "verificador": OTRO}), FUERA_DE_ALCANCE, "C9")

    def test_o1a_finca_de_una_medicion_suya(self):
        self.assertDecision(decidir("finca:leer", "VF", finca(verificadores=[S])), CONCEDE, "C9")
        self.assertDecision(decidir("finca:leer", "VF", finca(verificadores=[OTRO])), FUERA_DE_ALCANCE)

    def test_sin_o1b_ni_o3(self):
        self.assertDecision(decidir("propietario:leer", "VF", {"tipo": "datospropietario", "id": 1}), NIEGA, "RG1")
        self.assertDecision(decidir("asignacion:leer", "VF", {"tipo": "asignacion", "id": 1}), NIEGA, "RG1")


class C13Tests(Base):
    def periodo(self, estado, pendientes=0):
        return {"tipo": "periodo", "id": 1, "estado": estado, "pendientes": pendientes}

    def test_abrir(self):
        self.assertDecision(decidir("periodo:abrir", "GC", None, {"nombre": "2026-A"}), CONCEDE, "C13")
        self.assertDecision(decidir("periodo:abrir", "AC"), NIEGA, "RG1")

    def test_pasar_a_verificacion_solo_desde_abierto(self):
        self.assertDecision(decidir("periodo:pasar_verificacion", "GC", self.periodo("ABIERTO")), CONCEDE)
        for estado in ("EN_VERIFICACION", "CERRADO"):
            with self.subTest(estado=estado):
                self.assertDecision(decidir("periodo:pasar_verificacion", "GC", self.periodo(estado)), NIEGA, "C13")

    def test_cerrar_sin_pendientes(self):
        self.assertDecision(decidir("periodo:cerrar", "GC", self.periodo("EN_VERIFICACION")), CONCEDE)
        self.assertDecision(decidir("periodo:cerrar", "GC", self.periodo("EN_VERIFICACION", 2)), NIEGA, "C13")  # V16
        for estado in ("ABIERTO", "CERRADO"):
            with self.subTest(estado=estado):
                self.assertDecision(decidir("periodo:cerrar", "GC", self.periodo(estado)), NIEGA, "C13")

    def test_sin_atributos(self):
        self.assertDecision(decidir("periodo:cerrar", "GC", {"tipo": "periodo", "id": 1, "estado": "EN_VERIFICACION"}),
                            NIEGA, "RG5")
        self.assertDecision(decidir("periodo:cerrar", "GC"), NIEGA, "RG5")


class C19Tests(Base):
    def contexto(self, roles=("TC",), estado_periodo="ABIERTO", tecnico=20, propietario=OTRO):
        return {"tecnico": {"tipo": "usuario", "id": tecnico, "estado": "ACTIVA", "roles": list(roles)},
                "finca": finca(propietario=propietario),
                "periodo": {"tipo": "periodo", "id": 1, "estado": estado_periodo}}

    def test_crear(self):
        self.assertDecision(decidir("asignacion:crear", "AC", None, self.contexto()), CONCEDE, "C19")
        negadas = [self.contexto(roles=("VF",)), self.contexto(roles=()), self.contexto(estado_periodo="CERRADO"),
                   self.contexto(tecnico=20, propietario=20)]  # V17: el técnico es el propietario
        for contexto in negadas:
            with self.subTest(contexto=contexto):
                self.assertDecision(decidir("asignacion:crear", "AC", None, contexto), NIEGA, "C19")

    def test_crear_sin_contexto(self):
        self.assertDecision(decidir("asignacion:crear", "AC", None, {"tecnico": {"id": 1}}), NIEGA, "RG5")

    def test_terminar(self):
        asignacion = {"tipo": "asignacion", "id": 1, "tecnico": 20, "propietario": OTRO, "periodo_estado": "ABIERTO"}
        self.assertDecision(decidir("asignacion:terminar", "AC", asignacion), CONCEDE, "C19")
        self.assertDecision(decidir("asignacion:terminar", "AC", {**asignacion, "periodo_estado": "CERRADO"}), NIEGA)
        self.assertDecision(decidir("asignacion:terminar", "AC", {**asignacion, "propietario": 20}), NIEGA)

    def test_solo_ac(self):
        self.assertDecision(decidir("asignacion:crear", "GC", None, self.contexto()), NIEGA, "RG1")


class C20Tests(Base):
    def test_objeto_vigente(self):
        for accion in ("finca:modificar", "finca:baja", "parcela:crear"):
            with self.subTest(accion=accion):
                self.assertDecision(decidir(accion, "AC", finca()), CONCEDE, "C20")
                self.assertDecision(decidir(accion, "AC", finca(estado="BAJA")), NIEGA, "C20")
        parcela = finca(tipo="parcela")
        self.assertDecision(decidir("parcela:modificar_geometria", "AC", parcela), CONCEDE)
        self.assertDecision(decidir("parcela:baja", "AC", {**parcela, "estado": "BAJA"}), NIEGA)

    def test_crear_sin_objeto_previo(self):
        self.assertDecision(decidir("finca:crear", "AC", None, {"propietario": {"tipo": "usuario", "id": 3}}), CONCEDE)
        self.assertDecision(decidir("propietario:registrar_datos", "AC", None, {"propietario": {"id": 3}}), CONCEDE)

    def test_solo_ac(self):
        for rol in ("GC", "TC", "PF"):
            with self.subTest(rol=rol):
                self.assertDecision(decidir("finca:modificar", rol, finca(propietario=S)), NIEGA, "RG1")
