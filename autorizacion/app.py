from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from . import reglas

app = FastAPI(
    title="Verdeazul · Motor de autorización",
    docs_url=None,  # sin documentación interactiva expuesta
    redoc_url=None,
    openapi_url=None,
)


class Sujeto(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: int = Field(gt=0)
    estado: str = Field(max_length=10)
    roles: list[str] = Field(max_length=6)
    mfa_edad: float | None = None
    ventana: dict[str, Any] | None = None  # solo AE (C5): fechas, estado y quién aprobó


class SolicitudAutorizacion(BaseModel):
    """Atributos que arma el PEP (autorizacion.pep); nunca datos personales."""

    model_config = ConfigDict(extra="forbid")

    sujeto: Sujeto
    accion: str = Field(min_length=1, max_length=100)
    recurso: dict[str, Any] | None = None
    contexto: dict[str, Any] = Field(default_factory=dict)
    hbac: dict[str, Any] = Field(default_factory=dict)  # contadores y hora para H1–H3


@app.get("/healthz")
def healthz():
    """Healthcheck de Cloud Run: no depende de nadie."""
    return {"ok": True}


@app.post("/autorizar")
def autorizar(solicitud: SolicitudAutorizacion) -> dict:
    # No se registra el cuerpo: aunque no debería, podría traer datos de un recurso.
    return reglas.decidir(solicitud.model_dump()).como_dict()
