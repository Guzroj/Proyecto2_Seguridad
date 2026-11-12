"""
Motor de autorización (Persona 3) como servicio HTTP mínimo.

Django NUNCA ejecuta estas decisiones en su proceso: llama a este servicio por
red a través de autorizacion.cliente. Así el motor queda detrás de una frontera
de confianza real.

Autenticación entre servicios: el servicio se despliega en Cloud Run con
--no-allow-unauthenticated, de modo que la plataforma RECHAZA cualquier petición
que no traiga un token OIDC válido emitido para su URL. No hace falta validar
el token en el código; verificarlo aquí de nuevo sería defensa en profundidad.

Estado actual: FAIL-CLOSED (fallo cerrado). Mientras Persona 3 no implemente
las reglas, /autorizar deniega siempre y /usuarios_con_rol devuelve una lista
vacía. Nunca se concede por omisión.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(
    title="Verdeazul · Motor de autorización",
    docs_url=None,  # sin documentación interactiva expuesta
    redoc_url=None,
)

# Mensaje único de denegación mientras el motor no está implementado.
NO_IMPLEMENTADO = (
    "Motor de autorización no implementado (Persona 3): se deniega por defecto."
)


class SolicitudAutorizacion(BaseModel):
    """
    Cuerpo de POST /autorizar, con la misma información del gancho
    nucleo.ganchos.autorizar. `usuario`, `recurso` y `contexto` llegan ya
    serializados por autorizacion.cliente (IDs, tipo de recurso y datos simples).
    """

    usuario: dict
    accion: str
    recurso: dict | None = None
    contexto: dict = Field(default_factory=dict)


@app.get("/healthz")
def healthz():
    """Healthcheck de Cloud Run: no toca la base ni depende de nadie."""
    return {"ok": True}


@app.post("/autorizar")
def autorizar(solicitud: SolicitudAutorizacion):
    """
    Decide si `usuario` puede ejecutar `accion` sobre `recurso`.

    TODO Persona 3: implementar aquí el rol vigente, el alcance ABAC, la edad
    del segundo factor (MFA) y las reglas HBAC (H1–H3). Debe devolver 2xx si
    concede, 403 si niega (AccesoDenegado), 404 si el recurso está fuera de
    alcance (FueraDeAlcance) y 401 si solo falta un segundo factor reciente
    (ReautenticacionRequerida). Mientras tanto se deniega SIEMPRE.
    """
    # No se registra el cuerpo: puede contener datos personales.
    raise HTTPException(status_code=403, detail=NO_IMPLEMENTADO)


@app.get("/usuarios_con_rol/{rol}")
def usuarios_con_rol(rol: str):
    """
    Usuarios ACTIVOS con el rol vigente `rol` ("TC", "VF", "GC", ...).

    Misma firma que nucleo.ganchos.usuarios_con_rol: el cliente devuelve con
    estos IDs un QuerySet de Django. TODO Persona 3: consultar los roles y su
    vigencia en la base del motor. Por ahora, lista vacía (fallo cerrado: sin
    usuarios elegibles no se puede asignar ni aprobar nada).
    """
    return JSONResponse(content={"usuarios": []})
