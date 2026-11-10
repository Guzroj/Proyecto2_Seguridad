"""
Catálogo de fincas y parcelas (activo O1 de la Parte 1).

O1 se divide en dos columnas de la matriz:
  - O1a: Finca, Parcela, ParcelaVersion  (técnico y verificador la leen según C6/C9)
  - O1b: DatosPropietario                (datos personales, Ley 8968: solo AC, AE y el propio PF)

Reglas de diseño aplicadas aquí:
  - RG1: nada se borra físicamente; las bajas son cambios de estado
    (on_delete=PROTECT en todas las relaciones).
  - C20: la geometría de una parcela se VERSIONA. Modificarla cierra la vigencia
    de la versión actual y crea una nueva; una versión usada por una medición o
    un certificado nunca cambia.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class EstadoCatalogo(models.TextChoices):
    ACTIVA = "ACTIVA", "Activa"
    BAJA = "BAJA", "De baja"


class Finca(models.Model):
    """O1a. El propietario se guarda como identificador interno (FK)."""

    nombre = models.CharField(max_length=150)
    ubicacion = models.CharField(
        max_length=255, help_text="Provincia, cantón y distrito, o referencia de acceso."
    )
    propietario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fincas"
    )
    estado = models.CharField(
        max_length=10, choices=EstadoCatalogo.choices, default=EstadoCatalogo.ACTIVA
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["nombre"]

    def __str__(self) -> str:
        return self.nombre


class DatosPropietario(models.Model):
    """
    O1b. Datos personales del dueño de finca (Ley 8968), separados de Finca para
    que leer una finca (O1a) no exponga nombre, cédula ni contacto.
    Minimización: solo lo necesario para emitir certificados y contactar.
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="datos_propietario"
    )
    nombre_completo = models.CharField(max_length=200)
    identificacion = models.CharField(max_length=30, unique=True)
    telefono = models.CharField(max_length=30, blank=True)
    correo = models.EmailField(blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "datos del propietario"
        verbose_name_plural = "datos de propietarios"

    def __str__(self) -> str:
        return self.nombre_completo


class Parcela(models.Model):
    """Unidad que se mide y se certifica: a lo sumo un certificado vigente por parcela y período."""

    finca = models.ForeignKey(Finca, on_delete=models.PROTECT, related_name="parcelas")
    codigo = models.CharField(max_length=30)
    estado = models.CharField(
        max_length=10, choices=EstadoCatalogo.choices, default=EstadoCatalogo.ACTIVA
    )
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["finca", "codigo"]
        constraints = [
            models.UniqueConstraint(fields=["finca", "codigo"], name="parcela_codigo_unico_por_finca"),
        ]

    def __str__(self) -> str:
        return f"{self.finca} / {self.codigo}"

    def version_vigente(self) -> "ParcelaVersion | None":
        return self.versiones.filter(vigente_hasta__isnull=True).first()


def validar_poligono_geojson(valor):
    """Validación de lista blanca: un Polygon GeoJSON con anillo cerrado y coordenadas numéricas."""
    if not isinstance(valor, dict) or valor.get("type") != "Polygon":
        raise ValidationError("La geometría debe ser un Polygon GeoJSON.")
    anillos = valor.get("coordinates")
    if not isinstance(anillos, list) or not anillos:
        raise ValidationError("El polígono no tiene coordenadas.")
    for anillo in anillos:
        if not isinstance(anillo, list) or len(anillo) < 4:
            raise ValidationError("Cada anillo necesita al menos 4 puntos.")
        for punto in anillo:
            if (
                not isinstance(punto, list)
                or len(punto) != 2
                or not all(isinstance(c, (int, float)) for c in punto)
                or not (-180 <= punto[0] <= 180 and -90 <= punto[1] <= 90)
            ):
                raise ValidationError("Coordenada inválida: se espera [longitud, latitud].")
        if anillo[0] != anillo[-1]:
            raise ValidationError("El anillo del polígono debe cerrarse (primer punto = último).")


class ParcelaVersion(models.Model):
    """
    Versión inmutable de la geometría de una parcela (C20).
    Vigente = vigente_hasta IS NULL; solo puede haber una vigente por parcela.
    Las mediciones y certificados apuntan a la versión que usaron.
    """

    parcela = models.ForeignKey(Parcela, on_delete=models.PROTECT, related_name="versiones")
    numero = models.PositiveIntegerField()
    area_ha = models.DecimalField(max_digits=10, decimal_places=4)
    geometria = models.JSONField(validators=[validar_poligono_geojson])
    vigente_desde = models.DateTimeField()
    vigente_hasta = models.DateTimeField(null=True, blank=True)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["parcela", "-numero"]
        constraints = [
            models.UniqueConstraint(fields=["parcela", "numero"], name="parcelaversion_numero_unico"),
            models.UniqueConstraint(
                fields=["parcela"],
                condition=Q(vigente_hasta__isnull=True),
                name="parcelaversion_una_vigente",
            ),
            models.CheckConstraint(condition=Q(area_ha__gt=0), name="parcelaversion_area_positiva"),
            models.CheckConstraint(
                condition=Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gt=models.F("vigente_desde")),
                name="parcelaversion_vigencia_coherente",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.parcela} v{self.numero}"
