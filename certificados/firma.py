"""
Claves de firma de certificados (lado Django).

  CERTIFICADO_CLAVE_PRIVADA   Secreto. Semilla Ed25519 de 32 bytes en base64 (o una clave
                              PKCS#8 PEM). En Cloud Run llega desde Secret Manager como
                              variable de entorno (secreto `verdeazul-certificado-clave`); en
                              local, desde .env (ignorado por git). Nunca se escribe en el
                              código, en la base, en la bitácora ni en los registros.
  CERTIFICADO_CLAVES_CONFIABLES
                              No secreto. Huellas SHA-256 (hex, separadas por coma, punto y
                              coma o espacios) de las claves públicas que Verdeazul reconoce
                              como suyas: la actual y las anteriores (para verificar
                              certificados y sellos viejos tras rotar). Si está vacía, solo se
                              reconoce la clave actual.

Generar una clave nueva: `python manage.py generar_clave_certificados` (imprime la privada
para cargarla en Secret Manager y la huella para publicarla).

Fallo cerrado: sin clave válida no se emite; la emisión lanza y la aprobación se revierte.
"""

import base64
import binascii
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from django.conf import settings

from . import formato


class ClaveNoDisponible(Exception):
    """No hay una clave de firma utilizable (no se dice por qué con detalle: es un secreto)."""


def clave_privada() -> Ed25519PrivateKey:
    valor = (getattr(settings, "CERTIFICADO_CLAVE_PRIVADA", "") or "").strip()
    if not valor:
        raise ClaveNoDisponible("La clave de firma de certificados no está configurada.")
    try:
        if valor.startswith("-----BEGIN"):
            clave = load_pem_private_key(valor.encode("ascii"), password=None)
        else:
            clave = Ed25519PrivateKey.from_private_bytes(base64.b64decode(valor, validate=True))
    except (ValueError, TypeError, binascii.Error, UnicodeEncodeError):
        raise ClaveNoDisponible("La clave de firma de certificados no es válida.") from None
    if not isinstance(clave, Ed25519PrivateKey):
        raise ClaveNoDisponible("La clave de firma de certificados no es Ed25519.")
    return clave


def huella_actual() -> str:
    return formato.huella(formato.publica_cruda(clave_privada().public_key()))


def clave_publica_actual() -> str:
    return formato.b64(formato.publica_cruda(clave_privada().public_key()))


def huellas_confiables() -> set[str]:
    """Huellas reconocidas como de Verdeazul; nunca se toman del documento ni de la base."""
    configuradas = getattr(settings, "CERTIFICADO_CLAVES_CONFIABLES", "") or ""
    if isinstance(configuradas, str):
        # Se aceptan comas, punto y coma y espacios: Cloud Run/gcloud separa los
        # pares KEY=VALUE por comas, así que en el despliegue se publican con espacios.
        configuradas = re.split(r"[,;\s]+", configuradas)
    huellas = {h.strip().lower() for h in configuradas if h and h.strip()}
    if not huellas:
        try:
            huellas = {huella_actual()}
        except ClaveNoDisponible:
            huellas = set()
    return huellas


def nueva_clave() -> tuple[str, str]:
    """(privada en base64, huella) de una clave Ed25519 nueva."""
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    clave = Ed25519PrivateKey.generate()
    privada = clave.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.b64encode(privada).decode("ascii"), formato.huella(formato.publica_cruda(clave.public_key()))
