"""
Llaves públicas de los dispositivos de campo (O7c, C18).

Se acepta solo una llave PÚBLICA en PEM (SubjectPublicKeyInfo) de tipo Ed25519
o ECDSA P-256 (las dos que firman con WebCrypto en el navegador). Una llave
privada se rechaza sin guardarla ni registrarla en la bitácora (RG2).
"""

import hashlib

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from nucleo.errores import DatoInvalido

from .models import AlgoritmoLlave

MAX_LARGO_PEM = 2000


def normalizar_llave_publica(pem) -> tuple[str, str, str]:
    """Devuelve (algoritmo, pem_normalizado, huella_sha256_hex) o lanza DatoInvalido."""
    if not isinstance(pem, str) or not pem.strip() or len(pem) > MAX_LARGO_PEM:
        raise DatoInvalido("llave_publica: se espera una llave pública en formato PEM.")
    if "PRIVATE KEY" in pem.upper():
        raise DatoInvalido("llave_publica: se recibió una llave PRIVADA; solo se registra la pública.")
    try:
        llave = serialization.load_pem_public_key(pem.strip().encode("ascii"))
    except (ValueError, UnsupportedAlgorithm, UnicodeEncodeError):
        raise DatoInvalido("llave_publica: no es una llave pública PEM válida.")
    if isinstance(llave, ed25519.Ed25519PublicKey):
        algoritmo = AlgoritmoLlave.ED25519
    elif isinstance(llave, ec.EllipticCurvePublicKey) and isinstance(llave.curve, ec.SECP256R1):
        algoritmo = AlgoritmoLlave.P256
    else:
        raise DatoInvalido("llave_publica: solo se aceptan llaves Ed25519 o ECDSA P-256.")
    der = llave.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    normalizado = llave.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    return algoritmo, normalizado, hashlib.sha256(der).hexdigest()
