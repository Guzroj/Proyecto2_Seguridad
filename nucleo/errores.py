"""
Excepciones comunes.

Separación de responsabilidades entre capas:
  - ReglaNegocio y subclases: las lanza la capa de servicios (Persona 2) cuando una
    operación viola una invariante del dominio, sin importar el rol de quien la pide
    (p. ej. editar una medición aprobada, aprobar la propia medición).
  - AccesoDenegado / FueraDeAlcance: las lanza el motor de autorización (Persona 3)
    cuando el rol, el alcance, el segundo factor o una regla HBAC no permiten la acción.

Sugerencia para las vistas: AccesoDenegado -> 403, FueraDeAlcance -> 404 (RG3),
ReglaNegocio -> 409 (conflicto con el estado) o 400 (DatoInvalido).
"""


class ReglaNegocio(Exception):
    """Operación rechazada por una invariante del dominio."""


class TransicionInvalida(ReglaNegocio):
    """El estado actual del objeto no permite la transición pedida (máquina de estados, H4, C13)."""


class DatoInvalido(ReglaNegocio):
    """Entrada fuera de la lista blanca (tipo, rango, catálogo cerrado)."""


class SeparacionDeFunciones(ReglaNegocio):
    """La persona ya participó o tiene conflicto de interés (C2, C3, C19, doble control)."""


class SinAsignacion(ReglaNegocio):
    """El técnico no tiene asignación vigente para esa finca y período (C1)."""


class SinVerificadorDisponible(ReglaNegocio):
    """No queda ningún verificador elegible tras aplicar las exclusiones de C2."""


class AccesoDenegado(Exception):
    """Lo lanza el motor de autorización: el sujeto no puede hacer esto (HTTP 403)."""


class FueraDeAlcance(Exception):
    """El objeto no existe o está fuera del alcance del sujeto; no revelar cuál (HTTP 404, RG3)."""


class ReautenticacionRequerida(AccesoDenegado):
    """Solo falta un segundo factor reciente (<= 300 s): responder con reto, no con negación (RG6)."""
