"""
Cliente HTTP del motor de autorización (el "gancho real" de Persona 3).

Este módulo implementa la MISMA firma que los ganchos de nucleo/ganchos.py, para
que Django pueda llamar al servicio autorizador sin tocar los servicios. Cuando
Persona 3 active el motor, basta con apuntar en settings:

    VERDEAZUL_GANCHOS = {
        "autorizar":        "autorizacion.cliente.autorizar",
        "usuarios_con_rol": "autorizacion.cliente.usuarios_con_rol",
        ...
    }

La frontera de confianza es la red: destino privado de Cloud Run
(--no-allow-unauthenticated) y token OIDC del metadata server como credencial.
Regla de oro: cualquier fallo (URL sin configurar, timeout, error HTTP no
previsto) se traduce en DENEGAR, nunca en conceder.

Requiere `httpx`, que ya está en requirements.txt (imagen de Django).

Este módulo no se importa desde autorizacion/app.py: el motor no depende de
Django.
"""

import logging
import os
from urllib.parse import quote

import httpx
from django.contrib.auth import get_user_model

from nucleo.errores import AccesoDenegado, FueraDeAlcance, ReautenticacionRequerida

log = logging.getLogger("verdeazul.autorizacion")

# Token de identidad de la cuenta de servicio del contenedor (metadata server).
URL_IDENTIDAD = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/identity"
)

# Tiempo máximo de espera: una autorización colgada bloquea la operación de
# negocio, así que se corta pronto y se deniega (fallo cerrado).
TIEMPO_ESPERA = 5.0


def _url_motor() -> str:
    """URL interna de verdeazul-autorizador, sin barra final."""
    return os.environ.get("AUTORIZADOR_URL", "").strip().rstrip("/")


def _token_identidad(audiencia: str) -> str:
    """Pide al metadata server un token OIDC con `audiencia` (la URL del motor)."""
    respuesta = httpx.get(
        URL_IDENTIDAD,
        params={"audience": audiencia},
        headers={"Metadata-Flavor": "Google"},
        timeout=TIEMPO_ESPERA,
    )
    respuesta.raise_for_status()
    return respuesta.text.strip()


def _detalle(respuesta: httpx.Response) -> str:
    """Mensaje del motor; si no viene, uno genérico (sin filtrar cuerpos)."""
    try:
        detalle = respuesta.json().get("detail")
    except (ValueError, AttributeError):
        detalle = None
    return detalle or f"El motor de autorización respondió {respuesta.status_code}."


def _identidad(usuario) -> dict:
    """Identifica al sujeto por su PK; nunca envía el objeto ni sus datos."""
    datos = {"id": str(getattr(usuario, "pk", usuario))}
    nombre = getattr(usuario, "get_username", None)
    if callable(nombre):
        datos["username"] = nombre()
    return datos


def _simple(valor):
    """Convierte modelos, dicts y listas a JSON simple (IDs, no objetos)."""
    if valor is None or isinstance(valor, (bool, int, float, str)):
        return valor
    if isinstance(valor, dict):
        return {str(clave): _simple(v) for clave, v in valor.items()}
    if isinstance(valor, (list, tuple, set)):
        return [_simple(v) for v in valor]
    if hasattr(valor, "pk") and hasattr(valor, "_meta"):
        return {"tipo": valor._meta.model_name, "id": str(valor.pk)}
    return str(valor)


# ===========================================================================
# Ganchos (mismas firmas que nucleo.ganchos)
# ===========================================================================
def autorizar(usuario, accion: str, recurso=None, contexto: dict | None = None) -> None:
    """
    Pregunta al motor si `usuario` puede ejecutar `accion` sobre `recurso`.

    No devuelve nada si concede. Si niega, lanza las mismas excepciones de
    nucleo/errores.py que el resto del sistema:
      403 -> AccesoDenegado · 404 -> FueraDeAlcance · 401 -> ReautenticacionRequerida
    Un error de red o de configuración también deniega (fallo cerrado).
    """
    base = _url_motor()
    if not base:
        raise AccesoDenegado("AUTORIZADOR_URL no está configurada: se deniega por defecto.")

    try:
        token = _token_identidad(base)
        respuesta = httpx.post(
            f"{base}/autorizar",
            json={
                "usuario": _identidad(usuario),
                "accion": accion,
                "recurso": _simple(recurso),
                "contexto": _simple(contexto or {}),
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIEMPO_ESPERA,
        )
    except (httpx.HTTPError, OSError) as exc:
        log.warning("Motor de autorización inalcanzable: %s", exc)
        raise AccesoDenegado(
            "Motor de autorización inalcanzable: se deniega por defecto."
        ) from exc

    if respuesta.status_code == 401:
        raise ReautenticacionRequerida(_detalle(respuesta))
    if respuesta.status_code == 403:
        raise AccesoDenegado(_detalle(respuesta))
    if respuesta.status_code == 404:
        raise FueraDeAlcance(_detalle(respuesta))
    if 200 <= respuesta.status_code < 300:
        return None
    raise AccesoDenegado(_detalle(respuesta))


def usuarios_con_rol(rol: str):
    """
    QuerySet de usuarios ACTIVOS con el rol vigente `rol`.

    Devuelve un QuerySet (no una lista) porque los servicios hacen
    .filter(pk=...) y .exclude(pk__in=...) sobre el resultado. Si el motor no
    responde, devuelve un QuerySet vacío (fallo cerrado).
    """
    Usuario = get_user_model()
    base = _url_motor()
    if not base:
        log.warning("AUTORIZADOR_URL no configurada: sin usuarios para el rol %s.", rol)
        return Usuario.objects.none()

    try:
        token = _token_identidad(base)
        respuesta = httpx.get(
            f"{base}/usuarios_con_rol/{quote(rol, safe='')}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIEMPO_ESPERA,
        )
        respuesta.raise_for_status()
        ids = respuesta.json().get("usuarios", [])
        if not isinstance(ids, list):
            raise ValueError("la respuesta no trae una lista 'usuarios'")
        ids = [i for i in ids if isinstance(i, (int, str))]
    except (httpx.HTTPError, OSError, ValueError, AttributeError) as exc:
        log.warning("No se pudo consultar el rol %s en el motor: %s", rol, exc)
        return Usuario.objects.none()

    return Usuario.objects.filter(pk__in=ids, is_active=True)
