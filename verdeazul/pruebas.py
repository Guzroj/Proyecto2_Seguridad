"""
Runner de pruebas del proyecto.

Si la corrida no tiene CERTIFICADO_CLAVE_PRIVADA (lo normal en CI y en una máquina nueva),
genera una clave Ed25519 efímera solo para esta corrida: nunca se escribe en disco ni se
imprime, y no sirve fuera del proceso de pruebas. Así las pruebas ejercitan la firma real
sin depender de un secreto.
"""

from django.conf import settings
from django.test.runner import DiscoverRunner


class Runner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        if not getattr(settings, "CERTIFICADO_CLAVE_PRIVADA", ""):
            from certificados.firma import nueva_clave

            settings.CERTIFICADO_CLAVE_PRIVADA, _ = nueva_clave()
