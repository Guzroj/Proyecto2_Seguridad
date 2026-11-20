"""
La tabla de cobertura (docs/cobertura-matriz.md) está al día con la
matriz y todo lo que cita existe: cada función de "Dónde se aplica" y cada prueba.
"""

import unittest

try:
    import django  # noqa: F401
except ImportError:
    raise unittest.SkipTest("Django no está instalado en esta imagen")

import ast
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from autorizacion import matriz
from cuentas.management.commands import tabla_cobertura

RAIZ = Path(settings.BASE_DIR)


@lru_cache(maxsize=None)
def definiciones(archivo: str) -> dict[str, set[str]]:
    """{nombre de función o clase de primer nivel: métodos} de un archivo, sin importarlo."""
    arbol = ast.parse((RAIZ / archivo).read_text(encoding="utf-8"))
    nombres = {}
    for nodo in arbol.body:
        if isinstance(nodo, (ast.FunctionDef, ast.ClassDef)):
            nombres[nodo.name] = {n.name for n in getattr(nodo, "body", []) if isinstance(n, ast.FunctionDef)}
    return nombres


class TablaCoberturaTests(SimpleTestCase):
    def setUp(self):
        self.contenido = tabla_cobertura.generar()
        self.filas = [l for l in self.contenido.splitlines() if re.match(r"^\| \d+ \|", l)]

    def test_el_archivo_esta_al_dia(self):
        archivo = tabla_cobertura.ARCHIVO
        self.assertTrue(archivo.exists(), "Falta docs/cobertura-matriz.md: python manage.py tabla_cobertura")
        self.assertEqual(archivo.read_text(encoding="utf-8"), self.contenido,
                         "La tabla no está al día: python manage.py tabla_cobertura")

    def test_78_filas_con_la_celda_de_la_matriz(self):
        self.assertEqual(len(self.filas), 78)
        celdas = [(r, o) for r in matriz.MATRIZ for o in matriz.OBJETOS]
        for fila, (rol, objeto) in zip(self.filas, celdas):
            columnas = [c.strip() for c in fila.strip("|").split("|")]
            self.assertEqual((columnas[1], columnas[2].split(" ")[0]), (rol, objeto))
            self.assertEqual(columnas[3], tabla_cobertura.texto_celda(matriz.MATRIZ[rol][objeto]))
        self.assertIn("| 37 | GC | O7d · Segundo factor | L [C17]; CR [C21]; AU [C17]; RE [C17] |", self.contenido)

    def test_cada_funcion_citada_existe(self):
        citas = set(re.findall(r"`([\w/]+\.py):(\w+)", self.contenido))
        self.assertGreater(len(citas), 40)
        for archivo, nombre in sorted(citas):
            with self.subTest(cita=f"{archivo}:{nombre}"):
                self.assertIn(nombre, definiciones(archivo))

    def test_cada_prueba_citada_existe(self):
        citas = set(re.findall(r"`([\w/]+\.py)::(\w+)(?:\.(\w+))?`", self.contenido))
        self.assertGreater(len(citas), 50)
        for archivo, clase, metodo in sorted(citas):
            with self.subTest(prueba=f"{archivo}::{clase}.{metodo}"):
                metodos = definiciones(archivo)
                self.assertIn(clase, metodos)
                if metodo:
                    self.assertIn(metodo, metodos[clase])

    def test_cada_condicion_y_control_tiene_pruebas(self):
        usadas = {c for fila in matriz.MATRIZ.values() for celda in fila.values()
                  for p in celda.values() for c in (*p.condiciones, *p.hbac)}
        self.assertEqual(usadas - set(tabla_cobertura.POR_CONDICION), set())
        self.assertEqual(len([c for c in usadas if c.startswith("C")]), 22)
