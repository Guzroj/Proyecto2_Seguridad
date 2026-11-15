"""Pruebas del motor de autorización que no necesitan base de datos."""

import unittest
from itertools import combinations

from autorizacion import roles


class ParesExcluyentesTests(unittest.TestCase):
    ESPERADOS = {
        ("TC", "VF"), ("AC", "GC"), ("AC", "TC"), ("AC", "VF"),
        ("AE", "TC"), ("AE", "VF"), ("AE", "GC"), ("AE", "AC"),
    }

    def test_exactamente_los_pares_de_la_especificacion(self):
        for a, b in combinations(roles.ROLES, 2):
            with self.subTest(a=a, b=b):
                esperado = (a, b) in self.ESPERADOS or (b, a) in self.ESPERADOS
                self.assertEqual(roles.son_excluyentes(a, b), esperado)
                self.assertEqual(roles.son_excluyentes(b, a), esperado)

    def test_pf_es_compatible_con_todos(self):
        for r in roles.ROLES:
            self.assertFalse(roles.son_excluyentes("PF", r))

    def test_un_rol_no_se_excluye_a_si_mismo(self):
        for r in roles.ROLES:
            self.assertFalse(roles.son_excluyentes(r, r))

    def test_conflictos(self):
        self.assertEqual(roles.conflictos("AE", {"TC", "PF", "GC"}), {"TC", "GC"})
        self.assertEqual(roles.conflictos("PF", {"TC", "VF", "AE"}), set())

    def test_es_rol(self):
        self.assertTrue(roles.es_rol("GC"))
        for valor in ("gc", "XX", "", None, 3):
            self.assertFalse(roles.es_rol(valor))

    def test_modulo_no_depende_de_django(self):
        import ast
        import inspect

        arbol = ast.parse(inspect.getsource(roles))
        modulos = {
            n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(arbol) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in getattr(n, "names", [None])
        }
        self.assertFalse(any(m and m.startswith("django") for m in modulos))
