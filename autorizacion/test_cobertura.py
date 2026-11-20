"""
Paso 19 · Cobertura celda por celda: las 78 celdas de la matriz (6 roles × 13
objetos, con la corrección 3 de GC·O7d), cada operación y cada acción que cae en
ella, contra el motor real. Python puro: corre en Django y en el autorizador.

  - operación ausente de la celda ("—" o no listada)  ->  NIEGA por RG1
  - operación sin condiciones                          ->  CONCEDE con ese rol
  - operación con condiciones                          ->  el motor las evalúa (no es RG1);
                                                          cada Cx tiene sus pruebas propias
  - en una lista, las condiciones de alcance las filtra el PEP: solo cuentan las del sujeto (C5)
Y al revés: cada operación de cada celda tiene al menos una acción que la ejerce.
"""

import unittest

from autorizacion import matriz, reglas
from autorizacion.reglas import CONCEDE, NIEGA
from autorizacion.test_reglas import HBAC_NEUTRO

VENTANA_AE = {"estado": "APROBADA", "inicio": "2026-11-01", "fin": "2026-11-20", "hoy": "2026-11-11",
              "aprobada_por_gc": True, "aprobada_por_distinto": True}


# Operaciones de la matriz que hoy no tienen una acción propia en el código (ninguna:
# AC·O1b CR se cerró con propietario:crear_datos).
SIN_ACCION_PROPIA: set[tuple[str, str, str]] = set()


def acciones_de(objeto: str, operacion: str) -> list[str]:
    return sorted(a for a, destino in matriz.ACCIONES.items() if destino == (objeto, operacion))


def decidir(rol: str, accion: str):
    sujeto = {"id": 7, "estado": "ACTIVA", "roles": [rol], "mfa_edad": 10}
    if rol == "AE":
        sujeto["ventana"] = VENTANA_AE
    return reglas.decidir({"sujeto": sujeto, "accion": accion, "recurso": None,
                           "contexto": {"registros": 1}, "hbac": dict(HBAC_NEUTRO)})


class CeldaPorCeldaTests(unittest.TestCase):
    def test_cada_celda_de_la_matriz(self):
        celdas = revisadas = 0
        for rol, fila in matriz.MATRIZ.items():
            for objeto, celda in fila.items():
                celdas += 1
                for operacion in matriz.OPERACIONES:
                    for accion in acciones_de(objeto, operacion):
                        revisadas += 1
                        with self.subTest(rol=rol, objeto=objeto, operacion=operacion, accion=accion):
                            d = decidir(rol, accion)
                            permiso = celda.get(operacion)
                            if permiso is None:
                                self.assertEqual((d.resultado, d.regla), (NIEGA, "RG1"), d.motivo)
                                continue
                            condiciones = set(permiso.condiciones)
                            if accion in matriz.LISTAS:
                                condiciones -= matriz.CONDICIONES_DE_ALCANCE
                            if not condiciones or condiciones == {"C5"}:
                                self.assertEqual((d.resultado, d.rol), (CONCEDE, rol), d.motivo)
                            else:
                                self.assertNotEqual(d.regla, "RG1", d.motivo)
        self.assertEqual(celdas, 78)
        self.assertEqual(revisadas, len(matriz.MATRIZ) * len(matriz.ACCIONES))  # cada rol × cada acción

    def test_cada_operacion_de_cada_celda_tiene_una_accion(self):
        for rol, fila in matriz.MATRIZ.items():
            for objeto, celda in fila.items():
                for operacion in celda:
                    if (rol, objeto, operacion) in SIN_ACCION_PROPIA:
                        self.assertFalse(acciones_de(objeto, operacion))  # si aparece, quitarla de la lista
                        continue
                    with self.subTest(rol=rol, objeto=objeto, operacion=operacion):
                        self.assertTrue(acciones_de(objeto, operacion))

    def test_correccion_3_gc_lee_o7d_acotado_por_c17(self):
        self.assertEqual(matriz.permiso("GC", matriz.O7D, "L"), matriz.Permiso(("C17",), ()))
        self.assertEqual(decidir("GC", "factor:listar").resultado, CONCEDE)
        self.assertEqual(decidir("GC", "factor:leer").regla, "RG5")  # sin objeto, C17 no se puede evaluar
