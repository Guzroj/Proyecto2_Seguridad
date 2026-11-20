"""
Motor: la matriz como datos y la regla de decisión (RG1, RG3, RG5, RG6).

Python puro (unittest, sin Django): corre con `manage.py test` y también dentro
del contenedor del autorizador.
"""

import unittest
from unittest import mock

from autorizacion import matriz, reglas
from autorizacion.reglas import CONCEDE, FUERA_DE_ALCANCE, NIEGA, REAUTENTICAR

# La matriz del diseño transcrita otra vez, en otro formato, para comparar.
# "op Cx Hy" separadas por ";", "‡" = H2. Vacío = "—".
_AE = "L C5; X C5 H3"
ESPERADA = {
    "TC": {"O1a": "L C6 H1 ‡", "O1b": "", "O2": "L", "O3": "L C8",
           "O4": "L C10 H1 ‡; CR C1 H4; MO C1 H4; EV C1 H4", "O5": "SO C11; L C12", "O6": "",
           "O7a": "L C21", "O7b": "", "O7c": "", "O7d": "CR C21", "O8": "", "O9": ""},
    "VF": {"O1a": "L C9 H1 ‡", "O1b": "", "O2": "L", "O3": "",
           "O4": "L C9 H1 ‡; AP C2 H4; DV C2 H4", "O5": "", "O6": "",
           "O7a": "L C21", "O7b": "", "O7c": "", "O7d": "CR C21", "O8": "", "O9": ""},
    "GC": {"O1a": "L H1 ‡", "O1b": "", "O2": "L; AB C13; TV C13; CI C13", "O3": "L",
           "O4": "L H1 ‡; SI C16", "O5": "L; AU C3; RE C3", "O6": "L ‡",
           "O7a": "L; AU C14", "O7b": "L; AU C7; RE C7", "O7c": "", "O7d": "L C17; CR C21; AU C17; RE C17",
           "O8": "L", "O9": "L; RS C22"},
    "AC": {"O1a": "L H1 ‡; CR C20; MO C20; BA C20", "O1b": "L H1 ‡; CR C20; MO C20", "O2": "L",
           "O3": "L; CR C19; MO C19", "O4": "", "O5": "", "O6": "",
           "O7a": "L; CR C15; MO C15; BA C15", "O7b": "L; AR C7; RR C7", "O7c": "L; CR C18; RV",
           "O7d": "L; SO C17; CR C21", "O8": "", "O9": ""},
    "AE": {objeto: _AE for objeto in matriz.OBJETOS},
    "PF": {"O1a": "L C4", "O1b": "L C4", "O2": "", "O3": "", "O4": "L C4", "O5": "", "O6": "L C4",
           "O7a": "L C21", "O7b": "", "O7c": "", "O7d": "CR C21", "O8": "", "O9": ""},
}


def leer_celda(texto: str) -> dict:
    celda = {}
    for parte in filter(None, (p.strip() for p in texto.split(";"))):
        op, *etiquetas = parte.replace("‡", "H2").split()
        celda[op] = matriz.Permiso(
            condiciones=tuple(e for e in etiquetas if e.startswith("C")),
            hbac=tuple(sorted(e for e in etiquetas if e.startswith("H"))),
        )
    return celda


def normalizada(celda: dict) -> dict:
    return {op: matriz.Permiso(p.condiciones, tuple(sorted(p.hbac))) for op, p in celda.items()}


# Contadores HBAC sin nada anómalo: miércoles 10:00 en Costa Rica, sin historial.
HBAC_NEUTRO = {"hora": "2026-11-11T10:00:00-06:00", "h1_suspendido": False, "h1_resuelta_hace": None,
               "h1_lecturas": 0, "h1_media_propia": None, "h1_media_rol": {}, "h3_exportados": 0}


def solicitud(accion, *roles, estado="ACTIVA", mfa_edad=None, recurso=None, sujeto_id=7):
    return {"sujeto": {"id": sujeto_id, "estado": estado, "roles": list(roles), "mfa_edad": mfa_edad},
            "accion": accion, "recurso": recurso, "contexto": {}, "hbac": dict(HBAC_NEUTRO)}


class MatrizTests(unittest.TestCase):
    def test_78_celdas_iguales_al_diseno(self):
        celdas = 0
        for rol, fila in ESPERADA.items():
            for objeto, texto in fila.items():
                with self.subTest(rol=rol, objeto=objeto):
                    self.assertEqual(normalizada(matriz.MATRIZ[rol][objeto]), leer_celda(texto))
                celdas += 1
        self.assertEqual(celdas, 78)
        self.assertEqual(sum(len(fila) for fila in matriz.MATRIZ.values()), 78)

    def test_etiquetas_validas(self):
        for rol, fila in matriz.MATRIZ.items():
            for objeto, celda in fila.items():
                for op, p in celda.items():
                    with self.subTest(rol=rol, objeto=objeto, op=op):
                        self.assertIn(op, matriz.OPERACIONES)
                        self.assertTrue(set(p.condiciones) <= set(reglas.IDS_CONDICIONES))
                        self.assertTrue(set(p.hbac) <= set(matriz.CONTROLES_HBAC))

    def test_cada_accion_cae_en_alguna_celda(self):
        for accion, (objeto, op) in matriz.ACCIONES.items():
            with self.subTest(accion=accion):
                self.assertIn(objeto, matriz.OBJETOS)
                self.assertTrue(any(op in matriz.MATRIZ[r][objeto] for r in matriz.MATRIZ),
                                f"{accion} no tiene ninguna celda que la conceda")


class DecisionTests(unittest.TestCase):
    def test_o9_en_pantalla_solo_gc_y_ae(self):
        # Aunque RLS deja a cada sujeto leer sus alertas (para revisar su suspensión),
        # la matriz niega L de O9 a TC, VF, AC y PF.
        propia = {"tipo": "alerta", "id": 3, "sujeto": 7}
        for rol in ("TC", "VF", "AC", "PF"):
            for accion in ("alerta:leer", "alerta:exportar", "alerta:resolver"):
                with self.subTest(rol=rol, accion=accion):
                    d = reglas.decidir(solicitud(accion, rol, recurso=propia))
                    self.assertEqual((d.resultado, d.regla), (NIEGA, "RG1"))
        self.assertTrue(reglas.decidir(solicitud("alerta:leer", "GC", recurso=propia)).concede)

    def test_rg1_denegacion_por_defecto(self):
        casos = [
            solicitud("medicion:borrar", "GC"),          # acción que no existe (nadie elimina)
            solicitud("cuenta:activar", "AC"),           # reservada a un proceso
            solicitud("alerta:crear", "GC"),
            solicitud("periodo:leer"),                   # sin roles
            solicitud("periodo:leer", "XX"),             # rol desconocido
            solicitud("periodo:leer", "PF"),             # celda "—"
            solicitud("bitacora:leer", "AC"),
            solicitud("periodo:leer", "GC", estado="INACTIVA"),
            solicitud("periodo:leer", "GC", estado="EN_BAJA"),
        ]
        for datos in casos:
            with self.subTest(accion=datos["accion"], roles=datos["sujeto"]["roles"]):
                d = reglas.decidir(datos)
                self.assertEqual((d.resultado, d.regla), (NIEGA, "RG1"))

    def test_celdas_sin_condicion_conceden(self):
        for accion, rol in [("periodo:leer", "TC"), ("periodo:leer", "VF"), ("asignacion:leer", "GC"),
                            ("bitacora:leer", "GC"), ("cuenta:leer", "AC"), ("rol:leer", "GC"),
                            ("dispositivo:revocar", "AC"), ("factor:leer", "AC")]:
            with self.subTest(accion=accion, rol=rol):
                d = reglas.decidir(solicitud(accion, rol))
                self.assertEqual((d.resultado, d.rol), (CONCEDE, rol))

    def test_devuelve_los_controles_hbac_de_la_celda(self):
        self.assertEqual(reglas.decidir(solicitud("finca:leer", "GC")).hbac, ("H1", "H2"))
        self.assertEqual(reglas.decidir(solicitud("certificado:leer", "GC")).hbac, ("H2",))

    def test_rg5_condicion_sin_implementar_niega(self):
        with mock.patch.dict(reglas.CONDICIONES, C15=reglas._no_implementada("C15")):
            d = reglas.decidir(solicitud("cuenta:crear", "AC"))
        self.assertEqual((d.resultado, d.regla), (NIEGA, "RG5"))
        self.assertIn("C15", d.motivo)

    def test_con_varios_roles_basta_una_concesion(self):
        d = reglas.decidir(solicitud("periodo:leer", "PF", "TC"))
        self.assertEqual((d.resultado, d.rol), (CONCEDE, "TC"))

    def test_solicitudes_mal_formadas(self):
        base = solicitud("periodo:leer", "GC")
        malas = [None, [], {}, {**base, "sujeto": None}, {**base, "accion": ""}, {**base, "accion": 3},
                 {**base, "recurso": "x"}, {**base, "contexto": []}]
        for campo, valor in [("id", "7"), ("id", True), ("id", 0), ("estado", None), ("roles", "GC"),
                             ("roles", [1]), ("mfa_edad", "10"), ("mfa_edad", True)]:
            malas.append({**base, "sujeto": {**base["sujeto"], campo: valor}})
        for datos in malas:
            with self.subTest(datos=str(datos)[:80]):
                d = reglas.decidir(datos)
                self.assertEqual((d.resultado, d.regla), (NIEGA, "RG5"))

    def test_mfa(self):
        hecha = reglas.leer_solicitud(solicitud("periodo:leer", "GC", mfa_edad=300))
        self.assertTrue(hecha.mfa)
        for edad in (None, 300.1, -1):
            self.assertFalse(reglas.leer_solicitud(solicitud("periodo:leer", "GC", mfa_edad=edad)).mfa)


class CombinacionTests(unittest.TestCase):
    """Cómo se combinan los resultados de las condiciones (con condiciones simuladas)."""

    def con(self, **condiciones):
        return mock.patch.dict(reglas.CONDICIONES, condiciones)

    @staticmethod
    def fija(evaluacion):
        return lambda solicitud, rol: evaluacion

    def test_rg3_fuera_del_alcance_de_todos_es_404(self):
        with self.con(C6=self.fija(reglas.fuera_de_alcance("C6"))):
            self.assertEqual(reglas.decidir(solicitud("finca:leer", "TC")).resultado, FUERA_DE_ALCANCE)

    def test_rg3_si_otro_rol_lo_ve_es_403(self):
        with self.con(C6=self.fija(reglas.fuera_de_alcance("C6")), C9=self.fija(reglas.niega("C9", "no"))):
            # TC y VF a la vez no es posible (E), pero la regla de combinación se prueba igual.
            d = reglas.decidir(solicitud("finca:leer", "TC", "VF"))
        self.assertEqual((d.resultado, d.regla), (NIEGA, "C9"))

    def test_rg6_solo_falta_el_segundo_factor(self):
        with self.con(C2=self.fija(reglas.reautenticar("C2"))):
            self.assertEqual(reglas.decidir(solicitud("medicion:aprobar", "VF")).resultado, REAUTENTICAR)

    def test_condicion_que_concede(self):
        with self.con(C15=self.fija(reglas.concede("C15"))):
            d = reglas.decidir(solicitud("cuenta:crear", "AC"))
        self.assertEqual((d.resultado, d.regla, d.rol), (CONCEDE, "C15", "AC"))

    def test_condiciones_defectuosas_niegan(self):
        def falla(s, r):
            raise RuntimeError("bug")

        def sin_atributo(s, r):
            raise reglas.AtributoFaltante("recurso.estado")

        for condicion in (falla, sin_atributo, lambda s, r: "CONCEDE", self.fija(reglas.Evaluacion("SI", "x"))):
            with self.subTest(condicion=condicion), self.con(C15=condicion):
                d = reglas.decidir(solicitud("cuenta:crear", "AC"))
                self.assertEqual((d.resultado, d.regla), (NIEGA, "RG5"))

    def test_una_prohibicion_prevalece_sobre_la_concesion(self):
        with mock.patch.object(reglas, "PROHIBICIONES", [lambda s: reglas.niega("H4", "secuencia")]):
            d = reglas.decidir(solicitud("periodo:leer", "GC"))
        self.assertEqual((d.resultado, d.regla), (NIEGA, "H4"))

    def test_prohibicion_defectuosa_niega(self):
        def falla(s):
            raise RuntimeError("bug")

        with mock.patch.object(reglas, "PROHIBICIONES", [falla]):
            self.assertEqual(reglas.decidir(solicitud("periodo:leer", "GC")).regla, "RG5")


class ListaTests(unittest.TestCase):
    """"<objeto>:listar": la celda y las condiciones del sujeto; el alcance lo filtra el PEP."""

    def test_concede_sin_objeto_y_devuelve_los_roles(self):
        d = reglas.decidir(solicitud("finca:listar", "PF"))                    # C4 lo filtra el PEP
        self.assertEqual((d.resultado, d.roles), (CONCEDE, ("PF",)))
        d = reglas.decidir(solicitud("finca:listar", "PF", "TC"))
        self.assertEqual((d.resultado, d.roles, d.rol, d.hbac), (CONCEDE, ("PF", "TC"), "TC", ("H1", "H2")))
        self.assertEqual(reglas.decidir(solicitud("finca:leer", "PF")).regla, "RG5")  # una lectura sí exige el objeto

    def test_sin_l_en_la_celda_niega(self):
        for accion, rol in (("alerta:listar", "TC"), ("asignacion:listar", "VF"), ("bitacora:listar", "AC"),
                            ("rol:listar", "PF")):
            with self.subTest(accion=accion, rol=rol):
                d = reglas.decidir(solicitud(accion, rol))
                self.assertEqual((d.resultado, d.regla), (NIEGA, "RG1"))

    def test_ae_necesita_ventana(self):
        ae = solicitud("medicion:listar", "AE")
        self.assertEqual(reglas.decidir(ae).resultado, NIEGA)                  # sin ventana: C5
        ae["sujeto"]["ventana"] = {"estado": "APROBADA", "inicio": "2026-11-01", "fin": "2026-11-20",
                                   "hoy": "2026-11-11", "aprobada_por_gc": True, "aprobada_por_distinto": True}
        self.assertEqual(reglas.decidir(ae).roles, ("AE",))

    def test_hbac_cuenta_la_lista_como_una_lectura(self):
        sol = solicitud("medicion:listar", "GC")
        sol["hbac"]["h1_lecturas"] = 25
        d = reglas.decidir(sol)
        self.assertEqual((d.resultado, d.regla, d.alertas[0]["control"]), (NIEGA, "H1", "H1"))
        sol = solicitud("finca:listar", "PF")
        sol["hbac"]["h1_suspendido"] = True
        self.assertEqual(reglas.decidir(sol).regla, "H1")                      # suspendido también como PF
