"""
Certificados de carbono (activo O6 de la Parte 1).

  EMITIDO (vigente) -> REVOCADO (P2). No hay otro estado ni se borra nunca: el histórico
  se conserva para la trazabilidad de 10 años.

Invariantes que hace cumplir la BASE (certificados/migrations/0002):
  - a lo sumo un EMITIDO por parcela y período (índice único parcial): cierra el
    "certificado emitido dos veces sobre la misma hectárea";
  - solo se inserta un EMITIDO de una medición APROBADA con la misma parcela, período,
    versión de geometría y toneladas, aprobada por el sujeto de la conexión;
  - la única modificación es EMITIDO -> REVOCADO por una versión enmendada aprobada de la
    misma parcela y período cuyas toneladas difieren; nada más cambia nunca;
  - nadie borra ni trunca la tabla.

`contenido` guarda el texto canónico exacto que se firmó: el documento se reconstruye
byte a byte para entregarlo y para verificarlo.
"""

import json
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from . import formato


class EstadoCertificado(models.TextChoices):
    EMITIDO = "EMITIDO", "Emitido (vigente)"
    REVOCADO = "REVOCADO", "Revocado"


class Certificado(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parcela = models.ForeignKey("catalogo.Parcela", on_delete=models.PROTECT, related_name="certificados")
    periodo = models.ForeignKey("mediciones.Periodo", on_delete=models.PROTECT, related_name="certificados")
    parcela_version = models.ForeignKey(
        "catalogo.ParcelaVersion", on_delete=models.PROTECT, related_name="certificados",
        help_text="Geometría certificada (C20: nunca cambia).",
    )
    medicion = models.ForeignKey(
        "mediciones.Medicion", on_delete=models.PROTECT, related_name="certificados",
        help_text="Medición aprobada que lo originó (la original o una versión enmendada).",
    )
    propietario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="certificados",
        help_text="Dueño de la finca al emitir (C4). La finca nunca cambia de dueño.",
    )
    toneladas_co2 = models.DecimalField(max_digits=12, decimal_places=4)
    estado = models.CharField(max_length=8, choices=EstadoCertificado.choices, default=EstadoCertificado.EMITIDO)
    emitido_en = models.DateTimeField()
    reemplaza_a = models.OneToOneField(
        "self", on_delete=models.PROTECT, related_name="reemplazado_por", null=True, blank=True,
        help_text="Certificado revocado al que sustituye (P2).",
    )
    revocado_en = models.DateTimeField(null=True, blank=True)
    revocado_por_medicion = models.ForeignKey(
        "mediciones.Medicion", on_delete=models.PROTECT, related_name="certificados_revocados",
        null=True, blank=True, help_text="Versión enmendada aprobada que cambió las toneladas.",
    )
    motivo_revocacion = models.CharField(max_length=300, blank=True)

    # Documento firmado (Ed25519). La clave privada nunca se guarda.
    contenido = models.TextField(help_text="JSON canónico exacto que se firmó.")
    firma = models.CharField(max_length=128)
    clave_publica = models.CharField(max_length=64)
    huella_clave = models.CharField(max_length=64)

    class Meta:
        verbose_name = "certificado"
        verbose_name_plural = "certificados"
        ordering = ["-emitido_en"]
        constraints = [
            models.UniqueConstraint(fields=["parcela", "periodo"], condition=Q(estado="EMITIDO"),
                                    name="certificado_vigente"),
            models.CheckConstraint(condition=Q(estado__in=["EMITIDO", "REVOCADO"]), name="certificado_estado_valido"),
            models.CheckConstraint(
                condition=(Q(estado="EMITIDO", revocado_en__isnull=True, revocado_por_medicion__isnull=True)
                           | Q(estado="REVOCADO", revocado_en__isnull=False, revocado_por_medicion__isnull=False)),
                name="certificado_revocacion_coherente",
            ),
            models.CheckConstraint(condition=Q(toneladas_co2__gte=0), name="certificado_toneladas_no_negativas"),
        ]
        indexes = [models.Index(fields=["propietario", "estado"], name="certificado_propietario")]

    def __str__(self) -> str:
        return f"Certificado {str(self.id)[:8]} · {self.estado}"

    @property
    def contenido_dict(self) -> dict:
        return json.loads(self.contenido)

    def documento(self) -> dict:
        return {
            "formato": formato.FORMATO,
            "contenido": self.contenido_dict,
            "firma": {"algoritmo": formato.ALGORITMO, "clave_publica": self.clave_publica,
                      "huella_clave": self.huella_clave, "valor": self.firma},
        }

    def archivo(self) -> str:
        return formato.serializar(self.documento())
