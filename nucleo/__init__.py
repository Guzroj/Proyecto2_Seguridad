"""
Piezas compartidas entre las apps (no es una app de Django, no tiene modelos):

  errores.py   excepciones comunes (reglas de negocio, acceso denegado, fuera de alcance)
  acciones.py  vocabulario único de acciones ("medicion:aprobar", ...) que usa la autorización
  ganchos.py   puntos de enganche que Persona 3 y Persona 4 reemplazan por configuración
  transicion.py decorador que envuelve cada operación en una transacción y registra negaciones
"""
