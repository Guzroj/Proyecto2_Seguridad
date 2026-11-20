import base64

from allauth.account.adapter import DefaultAccountAdapter
from allauth.mfa.adapter import DefaultMFAAdapter
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings


class AdaptadorCuentas(DefaultAccountAdapter):
    """Sin registro público: las cuentas las crea AC (C15)."""

    def is_open_for_signup(self, request) -> bool:
        return False


class AdaptadorMFA(DefaultMFAAdapter):
    """
    RG2: los secretos TOTP y la semilla de códigos de recuperación se guardan
    cifrados (Fernet: AES-128-CBC + HMAC-SHA256) y nadie los puede leer de la base.
    Un TC, VF, GC o AC no puede quitarse el segundo factor; su restablecimiento
    pasa por el doble control de C17.
    """

    def _fernet(self):
        return Fernet(clave_cifrado_mfa())

    def encrypt(self, text: str) -> str:
        return self._fernet().encrypt(text.encode()).decode()

    def decrypt(self, encrypted_text: str) -> str:
        return self._fernet().decrypt(encrypted_text.encode()).decode()

    def can_delete_authenticator(self, authenticator) -> bool:
        from .mfa import exige_mfa

        return not exige_mfa(authenticator.user)


def clave_cifrado_mfa() -> bytes:
    """Clave Fernet: MFA_CLAVE_CIFRADO si existe; si no, derivada de SECRET_KEY con HKDF."""
    propia = getattr(settings, "MFA_CLAVE_CIFRADO", "")
    material = (propia or settings.SECRET_KEY).encode()
    derivada = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"verdeazul-mfa-v1").derive(material)
    return base64.urlsafe_b64encode(derivada)
