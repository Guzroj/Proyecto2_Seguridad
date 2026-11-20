"""
Bitácora de auditoría (activo O8 de la Parte 1): quién hizo qué, sobre qué y cuándo.

Cada evento queda encadenado con el anterior:

    hash_n = SHA-256(hash_{n-1} || can(e_n))

donde can(e_n) es el JSON canónico (jsonb) de todos sus campos. La secuencia, la hora,
hash_anterior y hash los fija la BASE DE DATOS en un disparador (bitacora/migrations/0002),
no la aplicación: un cliente no puede elegir su lugar en la cadena ni falsificar el
encadenamiento. La tabla es de solo inserción (disparadores contra UPDATE, DELETE y
TRUNCATE, aun para el dueño), tiene RLS con FORCE y solo GC y AE la leen.

La clave primaria es un UUID generado por la aplicación: así el INSERT no necesita
RETURNING, que bajo RLS exigiría que quien escribe (p. ej. un TC) pudiera leer la fila.
"""

import uuid

from django.conf import settings
from django.db import models


class Resultado(models.TextChoices):
    OK = "OK", "Cambio realizado"
    CONCEDIDO = "CONCEDIDO", "Lectura concedida"
    DENEGADO = "DENEGADO", "Denegado"


class Evento(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Los fija el disparador bitacora_encadenar (la aplicación no los puede elegir).
    secuencia = models.BigIntegerField(unique=True, null=True, editable=False)
    momento = models.DateTimeField(null=True, editable=False, help_text="Hora del servidor de base de datos (UTC).")
    hash_anterior = models.CharField(max_length=64, blank=True, editable=False)
    hash = models.CharField(max_length=64, blank=True, editable=False)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", null=True, blank=True,
        help_text="Cuenta que actuó; vacío para procesos del sistema (tarea diaria, motor HBAC).",
    )
    actor_nombre = models.CharField(max_length=150, blank=True)
    roles = models.JSONField(default=list, blank=True)
    accion = models.CharField(max_length=64)
    recurso_tipo = models.CharField(max_length=64, blank=True)
    recurso_id = models.CharField(max_length=64, blank=True)
    resultado = models.CharField(max_length=10, choices=Resultado.choices)
    antes = models.JSONField(null=True, blank=True)
    despues = models.JSONField(null=True, blank=True)
    detalle = models.CharField(max_length=1000, blank=True)
    origen = models.CharField(max_length=45, blank=True, help_text="Dirección IP de la petición, si la hubo.")

    class Meta:
        verbose_name = "evento de bitácora"
        verbose_name_plural = "eventos de bitácora"
        ordering = ["-secuencia"]
        indexes = [
            models.Index(fields=["accion"], name="bitacora_evento_accion"),
            models.Index(fields=["recurso_tipo", "recurso_id"], name="bitacora_evento_recurso"),
            models.Index(fields=["actor"], name="bitacora_evento_actor"),
        ]
        constraints = [
            # Una sola continuación por eslabón: dos eventos no pueden colgar del mismo anterior
            # (la cadena no se bifurca aunque dos transacciones escriban a la vez).
            models.UniqueConstraint(fields=["hash_anterior"], name="bitacora_sin_bifurcacion"),
            models.UniqueConstraint(fields=["hash"], name="bitacora_hash_unico"),
            models.CheckConstraint(condition=models.Q(resultado__in=["OK", "CONCEDIDO", "DENEGADO"]),
                                   name="bitacora_resultado_valido"),
        ]

    def __str__(self) -> str:
        return f"#{self.secuencia} {self.accion} {self.resultado}"


class SelloBitacora(models.Model):
    """
    Sello externo de la cabeza de la cadena (Arquitectura §4.5). Cada hora un proceso firma con
    Ed25519 (la clave de Verdeazul, que no está en la base) la secuencia y el hash del último
    evento, y lo deja aquí y en Cloud Logging. Quien administre la base puede reescribir toda la
    cadena y recalcular los hashes, pero no puede firmar un sello nuevo que coincida: el evento
    sellado deja de coincidir (o desaparece, si truncó la cadena) y la verificación lo delata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    secuencia = models.BigIntegerField(help_text="Secuencia del evento sellado (la cabeza al sellar).")
    hash = models.CharField(max_length=64, help_text="Hash de ese evento.")
    momento = models.DateTimeField()
    contenido = models.TextField(help_text="JSON canónico exacto que se firmó.")
    firma = models.CharField(max_length=128)
    huella_clave = models.CharField(max_length=64)

    class Meta:
        verbose_name = "sello de la bitácora"
        verbose_name_plural = "sellos de la bitácora"
        ordering = ["-secuencia", "-momento"]

    def __str__(self) -> str:
        return f"Sello de #{self.secuencia}"
