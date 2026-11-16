"""
C20: una geometría nueva no se traslapa con la versión vigente de otra parcela activa.
Se valida en catalogo.services con shapely (D2): interiores que se cruzan se rechazan;
compartir solo un borde está permitido (parcelas vecinas).
"""

from autorizacion.test_integracion_o1_o3 import EscenarioBase, poligono
from catalogo import services as catalogo
from catalogo.models import ParcelaVersion
from nucleo.errores import DatoInvalido


def cuadrado(x0, y0, lado=0.01) -> dict:
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + lado, y0], [x0 + lado, y0 + lado],
                                                [x0, y0 + lado], [x0, y0]]]}


class TraslapeTests(EscenarioBase):
    """La parcela P1 del escenario ocupa poligono(0)."""

    def test_rechaza_una_parcela_que_se_traslapa_con_otra_vigente(self):
        finca2 = catalogo.crear_finca(self.ac, "El Roble", "Sarapiquí", self.pf2)
        with self.assertRaisesRegex(DatoInvalido, "C20.*P1"):
            catalogo.crear_parcela(self.ac, finca2, codigo="R1", area_ha="1", geometria=poligono(0))
        self.assertFalse(finca2.parcelas.exists())  # nada quedó a medias

    def test_vecinas_que_solo_comparten_un_borde_si(self):
        catalogo.crear_parcela(self.ac, self.finca, codigo="A", area_ha="1", geometria=cuadrado(-80.0, 10.0))
        catalogo.crear_parcela(self.ac, self.finca, codigo="B", area_ha="1", geometria=cuadrado(-79.99, 10.0))
        with self.assertRaisesRegex(DatoInvalido, "C20"):  # medio cuadrado encima de A
            catalogo.crear_parcela(self.ac, self.finca, codigo="C", area_ha="1", geometria=cuadrado(-79.995, 10.0))

    def test_rechaza_un_poligono_que_se_cruza_consigo_mismo(self):
        corbata = {"type": "Polygon", "coordinates": [[[-70.0, 10.0], [-69.99, 10.01], [-69.99, 10.0],
                                                        [-70.0, 10.01], [-70.0, 10.0]]]}
        with self.assertRaisesRegex(DatoInvalido, "consigo mismo"):
            catalogo.crear_parcela(self.ac, self.finca, codigo="X", area_ha="1", geometria=corbata)

    def test_cambiar_la_propia_geometria_no_choca_consigo_misma_pero_si_con_otra(self):
        otra = catalogo.crear_parcela(self.ac, self.finca, codigo="P2", area_ha="1", geometria=poligono(1))
        version = catalogo.modificar_geometria(self.ac, self.parcela, area_ha="1.6", geometria=poligono(0))
        self.assertEqual(version.numero, 2)
        with self.assertRaisesRegex(DatoInvalido, "C20.*P2"):
            catalogo.modificar_geometria(self.ac, self.parcela, area_ha="1.6", geometria=poligono(1))
        self.assertEqual(ParcelaVersion.objects.filter(parcela=otra).count(), 1)

    def test_una_parcela_dada_de_baja_ya_no_bloquea_el_lugar(self):
        catalogo.dar_baja_parcela(self.ac, self.parcela)
        finca2 = catalogo.crear_finca(self.ac, "El Roble", "Sarapiquí", self.pf2)
        nueva = catalogo.crear_parcela(self.ac, finca2, codigo="R1", area_ha="1", geometria=poligono(0))
        self.assertEqual(nueva.versiones.count(), 1)
