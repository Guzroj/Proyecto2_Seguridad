"""
Formularios del catálogo: solo dan forma a la entrada (tipos y largos). Las reglas
las vuelven a aplicar los servicios, que son la única vía para cambiar datos.
"""

import json
import math
import re

from django import forms

from nucleo import ganchos

MAX_VERTICES = 1000
EJEMPLO_VERTICES = "10.000, -84.000\n10.010, -84.000\n10.010, -83.990\n10.000, -83.990"


def texto_a_poligono(texto: str) -> dict:
    """
    Convierte la geometría escrita en el formulario a un Polygon GeoJSON.

    Formato principal: un vértice por línea, "latitud, longitud" (como se anotan con un GPS),
    separados por coma, punto y coma o espacios. El anillo se cierra solo: no hace falta repetir
    el primer punto. También se acepta un Polygon GeoJSON completo (si el texto empieza con "{").
    Solo da forma a la entrada: la validez del polígono y el traslape (C20) los revisa el servicio.
    """
    texto = (texto or "").strip()
    if texto.startswith("{"):
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            raise forms.ValidationError("No es un GeoJSON válido.")

    puntos = []
    for numero, linea in enumerate((l.strip() for l in texto.splitlines()), start=1):
        if not linea:
            continue
        partes = [p for p in re.split(r"[,;\s]+", linea) if p]
        if len(partes) != 2:
            raise forms.ValidationError(f"Línea {numero}: se espera «latitud, longitud».")
        try:
            lat, lon = float(partes[0]), float(partes[1])
        except ValueError:
            raise forms.ValidationError(f"Línea {numero}: «{linea}» no son dos números.")
        if not (math.isfinite(lat) and math.isfinite(lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise forms.ValidationError(
                f"Línea {numero}: latitud entre -90 y 90 y longitud entre -180 y 180.")
        puntos.append([lon, lat])  # GeoJSON guarda [longitud, latitud]
        if len(puntos) > MAX_VERTICES:
            raise forms.ValidationError(f"A lo sumo {MAX_VERTICES} vértices.")

    if puntos and puntos[0] != puntos[-1]:
        puntos.append(list(puntos[0]))  # cierra el anillo
    if len(puntos) < 4:
        raise forms.ValidationError("Se necesitan al menos 3 vértices distintos.")
    return {"type": "Polygon", "coordinates": [puntos]}


def poligono_a_texto(geojson: dict) -> str:
    """El anillo exterior como líneas «latitud, longitud», sin repetir el punto de cierre."""
    try:
        anillo = list(geojson["coordinates"][0])
    except (KeyError, IndexError, TypeError):
        return ""
    if len(anillo) > 1 and anillo[0] == anillo[-1]:
        anillo = anillo[:-1]
    return "\n".join(f"{lat}, {lon}" for lon, lat in anillo)


class FincaNuevaForm(forms.Form):
    nombre = forms.CharField(label="Nombre", max_length=150)
    ubicacion = forms.CharField(label="Ubicación", max_length=255)
    propietario = forms.ModelChoiceField(label="Propietario (PF)", queryset=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["propietario"].queryset = ganchos.usuarios_con_rol("PF").order_by("username")


class FincaModificarForm(forms.Form):
    nombre = forms.CharField(label="Nombre", max_length=150)
    ubicacion = forms.CharField(label="Ubicación", max_length=255)


class GeometriaForm(forms.Form):
    area_ha = forms.DecimalField(label="Área (ha)", max_digits=10, decimal_places=4, min_value=0)
    geometria = forms.CharField(
        label="Vértices de la parcela (latitud, longitud), uno por línea", max_length=20000,
        widget=forms.Textarea(attrs={"rows": 8, "placeholder": EJEMPLO_VERTICES}),
        help_text="Mínimo 3 vértices, en orden alrededor de la parcela; no hace falta repetir el "
                  "primero. Ejemplo:<br><code>10.000, -84.000<br>10.010, -84.000<br>10.010, -83.990"
                  "<br>10.000, -83.990</code><br>También se acepta un Polygon GeoJSON completo.",
    )

    def clean_geometria(self):
        return texto_a_poligono(self.cleaned_data["geometria"])


class ParcelaNuevaForm(GeometriaForm):
    codigo = forms.CharField(label="Código", max_length=30)
    field_order = ["codigo", "area_ha", "geometria"]


class DatosPropietarioForm(forms.Form):
    nombre_completo = forms.CharField(label="Nombre completo", max_length=200)
    identificacion = forms.CharField(label="Identificación", max_length=30)
    telefono = forms.CharField(label="Teléfono", max_length=30, required=False)
    correo = forms.EmailField(label="Correo", max_length=254, required=False)
