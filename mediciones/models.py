"""
Flujo de medición y certificación (activos O2, O3, O4 y O5 de la Parte 1).

  O2  Periodo           ABIERTO -> EN_VERIFICACION -> CERRADO (final, nadie lo reabre)
  O3  Asignacion        técnico–finca–período con vigencia; nunca se borra (alimenta tecnicos(f,p))
  O4  Medicion          BORRADOR -> ENVIADA -> APROBADA (final) | DEVUELTA -> ENVIADA
      MedicionActor     historial de quién actuó sobre cada medición: actores(m). Solo inserción.
  O5  Enmienda          SOLICITADA -> AUTORIZADA | RECHAZADA;  AUTORIZADA -> APLICADA | DESCARTADA

Este archivo define datos y restricciones que la base de datos hace cumplir SIEMPRE,
sin importar quién llame. Las transiciones (quién puede y cuándo) viven en services.py
y la autorización por rol la decide el motor de autorización.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q

USUARIO = settings.AUTH_USER_MODEL


# ---------------------------------------------------------------------------
# O2 · Períodos de medición
# ---------------------------------------------------------------------------
class EstadoPeriodo(models.TextChoices):
    ABIERTO = "ABIERTO", "Abierto"
    EN_VERIFICACION = "EN_VERIFICACION", "En verificación"
    CERRADO = "CERRADO", "Cerrado"


class Periodo(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    estado = models.CharField(
        max_length=20, choices=EstadoPeriodo.choices, default=EstadoPeriodo.ABIERTO
    )
    abierto_por = models.ForeignKey(USUARIO, on_delete=models.PROTECT, related_name="+")
    abierto_en = models.DateTimeField(auto_now_add=True)
    en_verificacion_por = models.ForeignKey(
        USUARIO, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    en_verificacion_en = models.DateTimeField(null=True, blank=True)
    cerrado_por = models.ForeignKey(
        USUARIO, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    cerrado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-fecha_inicio"]
        constraints = [
            models.CheckConstraint(
                condition=Q(fecha_fin__gte=F("fecha_inicio")), name="periodo_fechas_coherentes"
            ),
            # Un período cerrado siempre sabe quién y cuándo lo cerró.
            models.CheckConstraint(
                condition=~Q(estado=EstadoPeriodo.CERRADO)
                | (Q(cerrado_por__isnull=False) & Q(cerrado_en__isnull=False)),
                name="periodo_cerrado_con_responsable",
            ),
        ]

    def __str__(self) -> str:
        return self.nombre


# ---------------------------------------------------------------------------
# O3 · Asignaciones técnico–finca–período
# ---------------------------------------------------------------------------
class Asignacion(models.Model):
    """
    Vigente = vigente_hasta IS NULL. "Modificar" una asignación solo termina su
    vigencia (C19); el historial se conserva porque C2 excluye como verificador a
    todo técnico que ESTUVO asignado a la finca y período, aunque ya no lo esté.
    """

    tecnico = models.ForeignKey(USUARIO, on_delete=models.PROTECT, related_name="asignaciones")
    finca = models.ForeignKey("catalogo.Finca", on_delete=models.PROTECT, related_name="asignaciones")
    periodo = models.ForeignKey(Periodo, on_delete=models.PROTECT, related_name="asignaciones")
    vigente_desde = models.DateTimeField(auto_now_add=True)
    vigente_hasta = models.DateTimeField(null=True, blank=True)
    creada_por = models.ForeignKey(USUARIO, on_delete=models.PROTECT, related_name="+")
    terminada_por = models.ForeignKey(
        USUARIO, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )

    class Meta:
        verbose_name = "asignación"
        verbose_name_plural = "asignaciones"
        constraints = [
            models.UniqueConstraint(
                fields=["tecnico", "finca", "periodo"],
                condition=Q(vigente_hasta__isnull=True),
                name="asignacion_una_vigente",
            ),
            models.CheckConstraint(
                condition=Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gt=F("vigente_desde")),
                name="asignacion_vigencia_coherente",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tecnico} → {self.finca} ({self.periodo})"


# ---------------------------------------------------------------------------
# Catálogo cerrado de motivos de devolución (C2: DV exige motivo del catálogo)
# ---------------------------------------------------------------------------
class MotivoDevolucion(models.Model):
    codigo = models.SlugField(max_length=40, unique=True)
    descripcion = models.CharField(max_length=200)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "motivo de devolución"
        verbose_name_plural = "motivos de devolución"

    def __str__(self) -> str:
        return self.descripcion


# ---------------------------------------------------------------------------
# O4 · Mediciones
# ---------------------------------------------------------------------------
class EstadoMedicion(models.TextChoices):
    BORRADOR = "BORRADOR", "Borrador"
    ENVIADA = "ENVIADA", "Enviada"
    APROBADA = "APROBADA", "Aprobada"
    DEVUELTA = "DEVUELTA", "Devuelta"


class Medicion(models.Model):
    """
    UUID como clave: no revela cuántas mediciones existen ni permite adivinar la
    siguiente (complementa, no reemplaza, la autorización por objeto).

    Una versión enmendada es otra fila con version_de = medición original; la
    original nunca se edita (H4). La enmienda que la originó: self.enmienda_origen.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    parcela = models.ForeignKey("catalogo.Parcela", on_delete=models.PROTECT, related_name="mediciones")
    parcela_version = models.ForeignKey(
        "catalogo.ParcelaVersion",
        on_delete=models.PROTECT,
        related_name="mediciones",
        help_text="Geometría usada; queda fija aunque la parcela cambie después (C20).",
    )
    periodo = models.ForeignKey(Periodo, on_delete=models.PROTECT, related_name="mediciones")
    autor = models.ForeignKey(USUARIO, on_delete=models.PROTECT, related_name="mediciones_autor")
    estado = models.CharField(
        max_length=10, choices=EstadoMedicion.choices, default=EstadoMedicion.BORRADOR
    )
    verificador = models.ForeignKey(
        USUARIO,
        on_delete=models.PROTECT,
        related_name="mediciones_a_verificar",
        null=True,
        blank=True,
        help_text="Lo asigna el sistema al azar al enviar (C2, R2).",
    )
    version_de = models.ForeignKey(
        "self", on_delete=models.PROTECT, related_name="versiones", null=True, blank=True
    )

    # --- Datos crudos de campo (el servidor recalcula el carbono a partir de ellos) ---
    numero_arboles = models.PositiveIntegerField()
    dap_promedio_cm = models.DecimalField(
        max_digits=6, decimal_places=2, help_text="Diámetro a la altura del pecho, promedio (cm)."
    )
    altura_promedio_m = models.DecimalField(max_digits=5, decimal_places=2)
    observaciones = models.TextField(blank=True)

    # --- Resultado calculado por el servidor, nunca enviado por el cliente ---
    toneladas_co2 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    # --- Devolución ---
    motivo_devolucion = models.ForeignKey(
        MotivoDevolucion, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    comentario_devolucion = models.TextField(blank=True)

    # --- Marcas de tiempo (hora del servidor) ---
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)
    enviada_en = models.DateTimeField(null=True, blank=True)
    aprobada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "medición"
        verbose_name_plural = "mediciones"
        ordering = ["-creada_en"]
        indexes = [
            models.Index(fields=["periodo", "estado"], name="medicion_periodo_estado"),
            models.Index(fields=["verificador", "estado"], name="medicion_verificador_estado"),
        ]
        constraints = [
            # V6: a lo sumo UNA medición original aprobada por parcela y período
            # (cierra el "certificado emitido dos veces sobre la misma hectárea").
            models.UniqueConstraint(
                fields=["parcela", "periodo"],
                condition=Q(estado=EstadoMedicion.APROBADA, version_de__isnull=True),
                name="medicion_una_original_aprobada",
            ),
            # Separación de responsabilidades: el verificador nunca es el autor.
            models.CheckConstraint(
                condition=Q(verificador__isnull=True) | ~Q(verificador=F("autor")),
                name="medicion_verificador_no_es_autor",
            ),
            # Enviada/aprobada/devuelta siempre tiene verificador asignado.
            models.CheckConstraint(
                condition=Q(estado=EstadoMedicion.BORRADOR) | Q(verificador__isnull=False),
                name="medicion_verificador_si_no_borrador",
            ),
            # Aprobada siempre tiene toneladas calculadas y fecha de aprobación.
            models.CheckConstraint(
                condition=~Q(estado=EstadoMedicion.APROBADA)
                | (Q(toneladas_co2__isnull=False) & Q(aprobada_en__isnull=False)),
                name="medicion_aprobada_completa",
            ),
            # Devuelta siempre tiene motivo del catálogo.
            models.CheckConstraint(
                condition=~Q(estado=EstadoMedicion.DEVUELTA) | Q(motivo_devolucion__isnull=False),
                name="medicion_devuelta_con_motivo",
            ),
            # Rangos físicos plausibles (validación también en la BD).
            models.CheckConstraint(condition=Q(numero_arboles__gt=0), name="medicion_arboles_positivo"),
            models.CheckConstraint(
                condition=Q(dap_promedio_cm__gt=0) & Q(dap_promedio_cm__lte=500),
                name="medicion_dap_rango",
            ),
            models.CheckConstraint(
                condition=Q(altura_promedio_m__gt=0) & Q(altura_promedio_m__lte=120),
                name="medicion_altura_rango",
            ),
        ]

    def __str__(self) -> str:
        return f"Medición {str(self.id)[:8]} · {self.parcela} · {self.periodo} · {self.estado}"

    @property
    def finca(self):
        return self.parcela.finca

    @property
    def es_version(self) -> bool:
        return self.version_de_id is not None


class AccionActor(models.TextChoices):
    CREAR = "CREAR", "Crear"
    MODIFICAR = "MODIFICAR", "Modificar"
    ENVIAR = "ENVIAR", "Enviar a verificación"
    APROBAR = "APROBAR", "Aprobar"
    DEVOLVER = "DEVOLVER", "Devolver"
    SINCRONIZAR = "SINCRONIZAR", "Sincronizar (baja de técnico)"
    SOLICITAR_ENMIENDA = "SOLICITAR_ENMIENDA", "Solicitar enmienda"
    AUTORIZAR_ENMIENDA = "AUTORIZAR_ENMIENDA", "Autorizar enmienda"
    HEREDADO = "HEREDADO", "Actor heredado de la medición original"


class MedicionActor(models.Model):
    """
    actores(m) de la matriz: TODOS los usuarios que alguna vez actuaron sobre m.
    Solo inserción (un disparador en la BD rechaza UPDATE y DELETE).
    C2 y C3 lo consultan para impedir que quien participó apruebe o autorice.
    """

    medicion = models.ForeignKey(Medicion, on_delete=models.PROTECT, related_name="actores")
    usuario = models.ForeignKey(USUARIO, on_delete=models.PROTECT, related_name="+")
    accion = models.CharField(max_length=20, choices=AccionActor.choices)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "actor de medición"
        verbose_name_plural = "actores de medición"
        ordering = ["fecha"]
        indexes = [models.Index(fields=["medicion", "usuario"], name="medicionactor_med_usr")]

    def __str__(self) -> str:
        return f"{self.usuario} {self.accion} {self.medicion_id}"


# ---------------------------------------------------------------------------
# O5 · Enmiendas
# ---------------------------------------------------------------------------
class EstadoEnmienda(models.TextChoices):
    SOLICITADA = "SOLICITADA", "Solicitada"
    AUTORIZADA = "AUTORIZADA", "Autorizada"
    RECHAZADA = "RECHAZADA", "Rechazada"
    APLICADA = "APLICADA", "Aplicada (versión aprobada)"
    DESCARTADA = "DESCARTADA", "Descartada (versión devuelta)"


class Enmienda(models.Model):
    """
    Única vía para corregir una medición aprobada o de un período cerrado (H4).
    Autorizarla (C3) crea la versión enlazada en ENVIADA, que pasa de nuevo por
    verificación independiente (C2). La original no cambia nunca.
    """

    medicion_original = models.ForeignKey(Medicion, on_delete=models.PROTECT, related_name="enmiendas")
    solicitante = models.ForeignKey(USUARIO, on_delete=models.PROTECT, related_name="enmiendas_solicitadas")
    justificacion = models.TextField()
    estado = models.CharField(
        max_length=12, choices=EstadoEnmienda.choices, default=EstadoEnmienda.SOLICITADA
    )
    solicitada_en = models.DateTimeField(auto_now_add=True)

    # Valores corregidos que propone el solicitante; al autorizar se copian a la versión.
    numero_arboles = models.PositiveIntegerField()
    dap_promedio_cm = models.DecimalField(max_digits=6, decimal_places=2)
    altura_promedio_m = models.DecimalField(max_digits=5, decimal_places=2)
    observaciones = models.TextField(blank=True)

    autorizador = models.ForeignKey(
        USUARIO, on_delete=models.PROTECT, related_name="enmiendas_resueltas", null=True, blank=True,
        help_text="GC que autorizó o rechazó.",
    )
    resuelta_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.TextField(blank=True)

    version = models.OneToOneField(
        Medicion, on_delete=models.PROTECT, related_name="enmienda_origen", null=True, blank=True,
        help_text="Versión enlazada creada al autorizar.",
    )

    class Meta:
        ordering = ["-solicitada_en"]
        constraints = [
            models.CheckConstraint(condition=~Q(justificacion=""), name="enmienda_justificacion_obligatoria"),
            # Doble control: quien solicita no autoriza.
            models.CheckConstraint(
                condition=Q(autorizador__isnull=True) | ~Q(autorizador=F("solicitante")),
                name="enmienda_autorizador_no_es_solicitante",
            ),
            # C11: a lo sumo una enmienda en curso por medición original.
            models.UniqueConstraint(
                fields=["medicion_original"],
                condition=Q(estado__in=["SOLICITADA", "AUTORIZADA"]),
                name="enmienda_una_en_curso",
            ),
            # Todo lo que ya no está SOLICITADA fue resuelto por alguien.
            models.CheckConstraint(
                condition=Q(estado="SOLICITADA")
                | (Q(autorizador__isnull=False) & Q(resuelta_en__isnull=False)),
                name="enmienda_resuelta_con_responsable",
            ),
            # Autorizada/aplicada/descartada siempre tiene su versión enlazada.
            models.CheckConstraint(
                condition=Q(estado__in=["SOLICITADA", "RECHAZADA"]) | Q(version__isnull=False),
                name="enmienda_autorizada_con_version",
            ),
        ]

    def __str__(self) -> str:
        return f"Enmienda {self.pk} de {self.medicion_original_id} · {self.estado}"
