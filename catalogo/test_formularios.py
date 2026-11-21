"""
Geometría escrita como vértices «latitud, longitud» en los formularios del catálogo.
El formulario solo convierte a Polygon GeoJSON; la validez y el traslape (C20) los revisa el
servicio (catalogo/test_traslape.py).
"""

from django import forms
from django.test import SimpleTestCase

from catalogo.formularios import GeometriaForm, poligono_a_texto, texto_a_poligono

CUADRADO = "10.000, -84.000\n10.010, -84.000\n10.010, -83.990\n10.000, -83.990"


class TextoAPoligonoTests(SimpleTestCase):
    def test_vertices_a_geojson_cerrado_y_en_orden_longitud_latitud(self):
        g = texto_a_poligono(CUADRADO)
        self.assertEqual(g["type"], "Polygon")
        anillo = g["coordinates"][0]
        self.assertEqual(anillo[0], [-84.0, 10.0])          # GeoJSON: [longitud, latitud]
        self.assertEqual(anillo[0], anillo[-1])             # se cierra solo
        self.assertEqual(len(anillo), 5)

    def test_separadores_y_lineas_en_blanco(self):
        texto = "  10 -84\n\n10.01;-84\n10.01 , -83.99  \n"
        self.assertEqual(texto_a_poligono(texto)["coordinates"][0],
                         [[-84.0, 10.0], [-84.0, 10.01], [-83.99, 10.01], [-84.0, 10.0]])

    def test_si_ya_viene_cerrado_no_se_duplica_el_cierre(self):
        texto = CUADRADO + "\n10.000, -84.000"
        self.assertEqual(len(texto_a_poligono(texto)["coordinates"][0]), 5)

    def test_tambien_acepta_geojson(self):
        g = {"type": "Polygon", "coordinates": [[[-84.0, 10.0], [-84.0, 10.01], [-83.99, 10.01], [-84.0, 10.0]]]}
        self.assertEqual(texto_a_poligono('{"type": "Polygon", "coordinates": [[[-84.0, 10.0], [-84.0, 10.01], '
                                          '[-83.99, 10.01], [-84.0, 10.0]]]}'), g)

    def test_entradas_invalidas(self):
        casos = {
            "menos de 3 vértices": "10, -84\n10.01, -84",
            "tres números en una línea": "10, -84, 5\n10.01, -84\n10.01, -83.99",
            "texto": "diez, -84\n10.01, -84\n10.01, -83.99",
            "latitud fuera de rango": "95, -84\n10.01, -84\n10.01, -83.99",
            "longitud fuera de rango": "10, -184\n10.01, -84\n10.01, -83.99",
            "no finito": "nan, -84\n10.01, -84\n10.01, -83.99",
            "vacío": "   ",
            "GeoJSON roto": '{"type": "Polygon"',
        }
        for nombre, texto in casos.items():
            with self.subTest(caso=nombre), self.assertRaises(forms.ValidationError):
                texto_a_poligono(texto)

    def test_ida_y_vuelta(self):
        self.assertEqual(texto_a_poligono(poligono_a_texto(texto_a_poligono(CUADRADO))), texto_a_poligono(CUADRADO))
        self.assertEqual(poligono_a_texto({"sin": "coordenadas"}), "")

    def test_formulario(self):
        form = GeometriaForm({"area_ha": "2.5", "geometria": CUADRADO})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["geometria"]["type"], "Polygon")
        self.assertFalse(GeometriaForm({"area_ha": "2.5", "geometria": "10, -84"}).is_valid())
