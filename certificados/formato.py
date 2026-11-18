"""
Formato, firma y verificación de los certificados de carbono (Ed25519).

Este módulo NO depende de Django ni de la base de datos: solo de `cryptography`. Es la
misma lógica que usa el servidor y la que puede correr cualquier comprador para
verificar un certificado sin acceso a los sistemas de Verdeazul:

    python verificar_certificado.py certificado.json --huella <huella publicada por Verdeazul>

(o `python -m certificados.formato ...` desde el repositorio).

Documento (JSON, UTF-8):

    {
      "formato": "verdeazul-certificado/1",
      "contenido": { ... datos del certificado, incluida la huella de la clave emisora ... },
      "firma": {
        "algoritmo": "Ed25519",
        "clave_publica": "<32 bytes en base64>",       # clave incrustada (enunciado §2.2)
        "huella_clave": "<SHA-256 hex de la clave>",
        "valor": "<64 bytes de firma en base64>"
      }
    }

Lo firmado es determinista: el prefijo de dominio "verdeazul-certificado/1\\n" seguido del
JSON canónico de `contenido` (claves ordenadas, sin espacios, UTF-8). Cualquier cambio en
`contenido` (toneladas, propietario, fechas...) invalida la firma.

Por qué no basta la clave incrustada: cualquiera puede firmar un documento falso con SU
clave e incrustarla. La verificación de autenticidad compara la huella de la clave del
documento con las huellas que Verdeazul publica por un canal autenticado (la página
/certificados/clave-publica/ servida por HTTPS y el README del repositorio). Por eso el
resultado separa "la firma es válida" (integridad) de "la clave es de Verdeazul"
(autenticidad); un certificado es auténtico solo si ambas se cumplen.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
from decimal import Decimal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

FORMATO = "verdeazul-certificado/1"
ALGORITMO = "Ed25519"
EMISOR = "Verdeazul Carbono S.A."
DOMINIO = (FORMATO + "\n").encode("utf-8")
TAMANO_MAXIMO = 64 * 1024  # un certificado ocupa ~2 KB; se rechaza cualquier cosa grande


class DocumentoInvalido(ValueError):
    """El archivo no es un certificado bien formado."""


# ---------------------------------------------------------------------------
# Codificación
# ---------------------------------------------------------------------------
def _json_valor(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    raise TypeError(f"Tipo no serializable en un certificado: {type(valor).__name__}")


def canonico(contenido: dict) -> bytes:
    """JSON canónico: claves ordenadas, separadores fijos, UTF-8 sin escapar. Determinista."""
    return json.dumps(contenido, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=_json_valor).encode("utf-8")


def mensaje(contenido: dict) -> bytes:
    return DOMINIO + canonico(contenido)


def b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode("ascii")


def de_b64(texto, nombre: str, largo: int | None = None) -> bytes:
    if not isinstance(texto, str):
        raise DocumentoInvalido(f"{nombre}: debe ser texto base64.")
    try:
        datos = base64.b64decode(texto.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise DocumentoInvalido(f"{nombre}: base64 inválido.")
    if largo is not None and len(datos) != largo:
        raise DocumentoInvalido(f"{nombre}: se esperaban {largo} bytes.")
    return datos


def publica_cruda(clave_publica: Ed25519PublicKey) -> bytes:
    return clave_publica.public_bytes(Encoding.Raw, PublicFormat.Raw)


def huella(clave_publica_cruda: bytes) -> str:
    """Identificador de la clave: SHA-256 (hex) de sus 32 bytes."""
    return hashlib.sha256(clave_publica_cruda).hexdigest()


def huella_legible(h: str) -> str:
    """La huella en grupos de 4 para compararla a ojo."""
    return " ".join(h[i:i + 4] for i in range(0, len(h), 4))


# ---------------------------------------------------------------------------
# Firma
# ---------------------------------------------------------------------------
def firmar(contenido: dict, clave_privada: Ed25519PrivateKey) -> dict:
    """Devuelve el documento completo firmado. `contenido` debe traer emisor.huella_clave."""
    cruda = publica_cruda(clave_privada.public_key())
    h = huella(cruda)
    if (contenido.get("emisor") or {}).get("huella_clave") != h:
        raise ValueError("El contenido debe declarar la huella de la clave que lo firma.")
    firma = clave_privada.sign(mensaje(contenido))
    return {
        "formato": FORMATO,
        "contenido": contenido,
        "firma": {"algoritmo": ALGORITMO, "clave_publica": b64(cruda), "huella_clave": h, "valor": b64(firma)},
    }


def serializar(documento: dict) -> str:
    """Texto del archivo que se entrega (legible; la firma no depende de este formato)."""
    return json.dumps(documento, ensure_ascii=False, indent=2, sort_keys=True, default=_json_valor) + "\n"


# ---------------------------------------------------------------------------
# Verificación
# ---------------------------------------------------------------------------
def _sin_duplicados(pares):
    claves = [k for k, _ in pares]
    if len(claves) != len(set(claves)):
        # Dos lectores distintos podrían ver valores distintos: se rechaza el documento.
        raise DocumentoInvalido("Claves JSON duplicadas.")
    return dict(pares)


def _rechazar_constante(nombre):
    raise DocumentoInvalido(f"Valor numérico no permitido: {nombre}.")


def leer(datos) -> dict:
    """Bytes o texto -> documento (dict). Rechaza tamaños grandes, JSON inválido y claves repetidas."""
    if isinstance(datos, str):
        datos = datos.encode("utf-8")
    if not isinstance(datos, (bytes, bytearray)):
        raise DocumentoInvalido("Se esperaba el contenido del archivo.")
    if len(datos) > TAMANO_MAXIMO:
        raise DocumentoInvalido("El archivo es demasiado grande para ser un certificado.")
    try:
        documento = json.loads(datos.decode("utf-8"), object_pairs_hook=_sin_duplicados,
                               parse_constant=_rechazar_constante)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise DocumentoInvalido("El archivo no es JSON válido en UTF-8.")
    if not isinstance(documento, dict):
        raise DocumentoInvalido("El documento debe ser un objeto JSON.")
    return documento


def verificar(documento, huellas_confiables=()) -> dict:
    """
    Verifica un certificado. Nunca lanza por un documento malo: lo informa.

    Devuelve:
      valido       firma correcta Y clave de Verdeazul (lo único que significa "auténtico")
      integro      la firma corresponde al contenido con la clave incrustada
      confiable    la huella de la clave incrustada está entre las publicadas por Verdeazul
      huella_clave huella de la clave incrustada (o "")
      contenido    el contenido (solo si es íntegro; si no, None)
      errores      lista de motivos legibles
    """
    resultado = {"valido": False, "integro": False, "confiable": False, "huella_clave": "",
                 "contenido": None, "errores": []}
    errores = resultado["errores"]
    try:
        if not isinstance(documento, dict):
            documento = leer(documento)
        if documento.get("formato") != FORMATO:
            raise DocumentoInvalido(f"Formato desconocido (se esperaba {FORMATO}).")
        if set(documento) != {"formato", "contenido", "firma"}:
            raise DocumentoInvalido("El documento tiene campos de más o de menos.")
        contenido, firma = documento["contenido"], documento["firma"]
        if not isinstance(contenido, dict) or not isinstance(firma, dict):
            raise DocumentoInvalido("contenido y firma deben ser objetos.")
        if firma.get("algoritmo") != ALGORITMO:
            raise DocumentoInvalido("Algoritmo de firma no admitido.")
        cruda = de_b64(firma.get("clave_publica"), "firma.clave_publica", 32)
        valor = de_b64(firma.get("valor"), "firma.valor", 64)
        h = huella(cruda)
        resultado["huella_clave"] = h
        if firma.get("huella_clave") != h:
            raise DocumentoInvalido("La huella declarada no corresponde a la clave incrustada.")
        if (contenido.get("emisor") or {}).get("huella_clave") != h:
            raise DocumentoInvalido("El contenido no declara la clave que lo firma.")
        try:
            Ed25519PublicKey.from_public_bytes(cruda).verify(valor, mensaje(contenido))
        except InvalidSignature:
            raise DocumentoInvalido("La firma NO corresponde al contenido: el certificado fue alterado "
                                    "o no lo firmó esa clave.")
        except (TypeError, ValueError):
            raise DocumentoInvalido("El contenido no se puede canonizar.")
    except DocumentoInvalido as exc:
        errores.append(str(exc))
        return resultado
    resultado["integro"] = True
    resultado["contenido"] = contenido
    confiables = {str(x).strip().lower() for x in huellas_confiables if str(x).strip()}
    resultado["confiable"] = h in confiables
    if not confiables:
        errores.append("No se indicó ninguna huella de Verdeazul: no se puede confirmar quién emitió el certificado.")
    elif not resultado["confiable"]:
        errores.append("La clave que firmó NO es una clave publicada por Verdeazul.")
    resultado["valido"] = resultado["integro"] and resultado["confiable"]
    return resultado


# ---------------------------------------------------------------------------
# Verificador independiente (línea de comandos)
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verifica un certificado de carbono de Verdeazul (Ed25519).")
    parser.add_argument("archivo", help="Archivo JSON del certificado.")
    parser.add_argument("--huella", action="append", default=[],
                        help="Huella SHA-256 de una clave pública de Verdeazul (de su sitio HTTPS). Repetible.")
    args = parser.parse_args(argv)
    with open(args.archivo, "rb") as f:
        datos = f.read(TAMANO_MAXIMO + 1)
    r = verificar(datos, [h.replace(" ", "") for h in args.huella])
    if r["valido"]:
        c = r["contenido"]
        print("VÁLIDO: la firma es correcta y la clave es de Verdeazul.")
        print(f"  Certificado {c.get('certificado', {}).get('id')} · {c.get('toneladas_co2')} t CO2")
    else:
        print("NO VÁLIDO")
        for e in r["errores"]:
            print(f"  - {e}")
    print(f"  Huella de la clave del documento: {huella_legible(r['huella_clave']) or '—'}")
    return 0 if r["valido"] else 1


if __name__ == "__main__":
    sys.exit(main())
