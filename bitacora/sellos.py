"""
Sellos externos de la bitácora (Arquitectura §4.5).

    sellar()          firma la cabeza de la cadena (secuencia y hash del último evento) con la
                      clave Ed25519 de Verdeazul y guarda el sello (solo inserción). El comando
                      `manage.py sellar_bitacora` además lo escribe en la salida estándar, que en
                      Cloud Run va a Cloud Logging: una copia fuera de la base.
    verificar()       comprueba cada sello: firma válida con una clave de Verdeazul, y que el
                      evento sellado siga existiendo con el mismo hash.

Qué agrega a la cadena de hashes: la cadena sola detecta un cambio aislado, pero quien
administre la base podría reescribirla ENTERA y recalcular todos los hashes. No puede, en
cambio, firmar sellos nuevos (la clave privada no está en la base), así que el evento sellado
deja de coincidir o desaparece (si truncó el final). Borrar sellos de la tabla no alcanza:
siguen en Cloud Logging, y el resumen diario los vuelve a comparar.

La firma usa un prefijo de dominio distinto al de los certificados: un sello no puede hacerse
pasar por certificado ni al revés.
"""

import base64
import binascii
import json
from datetime import timezone as dt_timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.db import transaction
from django.utils import timezone

from certificados import firma, formato

from .models import Evento, SelloBitacora

FORMATO_SELLO = "verdeazul-sello-bitacora/1"
DOMINIO = (FORMATO_SELLO + "\n").encode("utf-8")


def _mensaje(contenido: dict) -> bytes:
    return DOMINIO + formato.canonico(contenido)


def sellar() -> SelloBitacora | None:
    """Sella la cabeza actual. None si la bitácora está vacía. Lanza ClaveNoDisponible sin clave."""
    clave = firma.clave_privada()
    cruda = formato.publica_cruda(clave.public_key())
    with transaction.atomic():
        cabeza = Evento.objects.order_by("-secuencia").values("secuencia", "hash").first()
        if cabeza is None:
            return None
        momento = timezone.now()
        contenido = {
            "formato": FORMATO_SELLO,
            "emisor": formato.EMISOR,
            "secuencia": cabeza["secuencia"],
            "hash": cabeza["hash"],
            "momento": momento.astimezone(dt_timezone.utc).isoformat(),
            "clave_publica": formato.b64(cruda),
        }
        return SelloBitacora.objects.create(
            secuencia=cabeza["secuencia"], hash=cabeza["hash"], momento=momento,
            contenido=formato.canonico(contenido).decode("utf-8"),
            firma=formato.b64(clave.sign(_mensaje(contenido))), huella_clave=formato.huella(cruda),
        )


def verificar_sello(sello: SelloBitacora, huellas_confiables=None) -> list[str]:
    """Problemas de un sello (vacío = correcto). No mira la cadena, solo la firma y el contenido."""
    huellas = firma.huellas_confiables() if huellas_confiables is None else set(huellas_confiables)
    problemas = []
    try:
        contenido = json.loads(sello.contenido)
        cruda = base64.b64decode(contenido["clave_publica"], validate=True)
        Ed25519PublicKey.from_public_bytes(cruda).verify(base64.b64decode(sello.firma, validate=True),
                                                          _mensaje(contenido))
    except (ValueError, KeyError, TypeError, binascii.Error, InvalidSignature):
        return ["la firma del sello no es válida (sello falsificado o alterado)"]
    if formato.huella(cruda) not in huellas or sello.huella_clave != formato.huella(cruda):
        problemas.append("el sello no lo firmó una clave de Verdeazul")
    if (contenido.get("secuencia"), contenido.get("hash")) != (sello.secuencia, sello.hash):
        problemas.append("las columnas del sello no coinciden con lo firmado")
    return problemas


def verificar(sellos=None) -> list[tuple[int, str]]:
    """Problemas de la cadena frente a los sellos: [(secuencia, problema)]. Vacío = coincide."""
    sellos = list(SelloBitacora.objects.order_by("secuencia")) if sellos is None else list(sellos)
    if not sellos:
        return []
    huellas = firma.huellas_confiables()
    hashes = dict(Evento.objects.filter(secuencia__in={s.secuencia for s in sellos})
                  .values_list("secuencia", "hash"))
    problemas = []
    for sello in sellos:
        for problema in verificar_sello(sello, huellas):
            problemas.append((sello.secuencia, f"sello: {problema}"))
        if sello.secuencia not in hashes:
            problemas.append((sello.secuencia, "sello: el evento sellado ya no existe (cadena truncada o borrada)"))
        elif hashes[sello.secuencia] != sello.hash:
            problemas.append((sello.secuencia, "sello: el evento sellado cambió (cadena reescrita)"))
    return problemas

