"""
Cálculo de toneladas de CO2 equivalente a partir de los datos crudos de campo.

La Parte 1 solo exige que "el servidor recalcula el carbono con los datos crudos"
(Arquitectura, F5 y Anexo C); no fija la fórmula. Decisión de implementación:

  1. Biomasa aérea por árbol (kg), ecuación pantropical de Chave et al. (2014):
         AGB = 0.0673 * (rho * D^2 * H) ^ 0.976
     D = diámetro a la altura del pecho (cm), H = altura (m), rho = densidad de la
     madera (g/cm^3; 0.6 por defecto, valor típico de bosque tropical).
  2. Carbono = 47 % de la biomasa seca (fracción de carbono del IPCC, 2006).
  3. CO2 = carbono * 44/12 (relación de masas moleculares CO2/C).

Total = número de árboles * AGB por árbol del árbol promedio.

Referencias:
  Chave, J. et al. (2014). Improved allometric models to estimate the aboveground
  biomass of tropical trees. Global Change Biology, 20(10), 3177–3190.
  https://doi.org/10.1111/gcb.12629
  IPCC (2006). 2006 IPCC Guidelines for National Greenhouse Gas Inventories, Vol. 4.

Simplificación aceptada: se usa el árbol promedio (no árbol por árbol) y se estima
el stock en pie, no el incremento entre períodos. Cambiar la fórmula solo requiere
modificar esta función.
"""

from decimal import ROUND_HALF_UP, Decimal

DENSIDAD_MADERA_DEFECTO = 0.6   # g/cm^3
FRACCION_CARBONO = 0.47
CO2_POR_CARBONO = 44 / 12
CUATRO_DECIMALES = Decimal("0.0001")


def biomasa_arbol_kg(dap_cm: float, altura_m: float, densidad: float = DENSIDAD_MADERA_DEFECTO) -> float:
    return 0.0673 * (densidad * dap_cm**2 * altura_m) ** 0.976


def calcular_toneladas_co2(
    numero_arboles: int,
    dap_promedio_cm: Decimal,
    altura_promedio_m: Decimal,
    densidad: float = DENSIDAD_MADERA_DEFECTO,
) -> Decimal:
    biomasa_total_kg = numero_arboles * biomasa_arbol_kg(float(dap_promedio_cm), float(altura_promedio_m), densidad)
    toneladas_co2 = biomasa_total_kg / 1000 * FRACCION_CARBONO * CO2_POR_CARBONO
    return Decimal(str(toneladas_co2)).quantize(CUATRO_DECIMALES, rounding=ROUND_HALF_UP)
