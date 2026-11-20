"""
Formularios del catálogo: solo dan forma a la entrada (tipos y largos). Las reglas
las vuelven a aplicar los servicios, que son la única vía para cambiar datos.
"""

import json

from django import forms

from nucleo import ganchos


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
        label="Geometría (Polygon GeoJSON)", max_length=20000,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text='Ejemplo: {"type": "Polygon", "coordinates": [[[-84.0, 10.0], [-84.0, 10.01], '
                  '[-83.99, 10.01], [-84.0, 10.0]]]}',
    )

    def clean_geometria(self):
        try:
            return json.loads(self.cleaned_data["geometria"])
        except json.JSONDecodeError:
            raise forms.ValidationError("No es un JSON válido.")


class ParcelaNuevaForm(GeometriaForm):
    codigo = forms.CharField(label="Código", max_length=30)
    field_order = ["codigo", "area_ha", "geometria"]


class DatosPropietarioForm(forms.Form):
    nombre_completo = forms.CharField(label="Nombre completo", max_length=200)
    identificacion = forms.CharField(label="Identificación", max_length=30)
    telefono = forms.CharField(label="Teléfono", max_length=30, required=False)
    correo = forms.EmailField(label="Correo", max_length=254, required=False)
