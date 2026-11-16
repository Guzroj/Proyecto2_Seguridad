"""
Formularios de las pantallas de cuentas: solo dan forma a la entrada (tipos y
largos). Las reglas las vuelven a aplicar los servicios, que son la única vía
para cambiar datos.
"""

from django import forms

from autorizacion import roles
from nucleo import ganchos

from .models import MetodoVerificacion

class CuentaNuevaForm(forms.Form):
    username = forms.CharField(label="Usuario", max_length=150)
    rol = forms.ChoiceField(label="Rol", choices=list(roles.ROLES.items()))
    first_name = forms.CharField(label="Nombre", max_length=150, required=False)
    last_name = forms.CharField(label="Apellidos", max_length=150, required=False)
    email = forms.EmailField(label="Correo", max_length=254, required=False)
    inicio = forms.DateField(label="Inicio de la ventana (solo AE)", required=False,
                             widget=forms.DateInput(attrs={"type": "date"}))
    fin = forms.DateField(label="Fin de la ventana (solo AE, máximo 30 días)", required=False,
                          widget=forms.DateInput(attrs={"type": "date"}))


class CuentaModificarForm(forms.Form):
    first_name = forms.CharField(label="Nombre", max_length=150, required=False)
    last_name = forms.CharField(label="Apellidos", max_length=150, required=False)
    email = forms.EmailField(label="Correo", max_length=254, required=False)


class AsignarRolForm(forms.Form):
    rol = forms.ChoiceField(label="Rol", choices=list(roles.ROLES.items()))


class MotivoForm(forms.Form):
    motivo = forms.CharField(label="Motivo", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}))


class MotivoRequeridoForm(forms.Form):
    motivo = forms.CharField(label="Motivo", max_length=500, widget=forms.Textarea(attrs={"rows": 3}))


class DispositivoForm(forms.Form):
    responsable = forms.ModelChoiceField(label="Responsable (TC)", queryset=None)
    etiqueta = forms.CharField(label="Etiqueta", max_length=100)
    llave_publica = forms.CharField(label="Llave pública (PEM, Ed25519 o P-256)", max_length=2000,
                                    widget=forms.Textarea(attrs={"rows": 6}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsable"].queryset = ganchos.usuarios_con_rol("TC").order_by("username")


class VerificacionForm(forms.Form):
    metodo_verificacion = forms.ChoiceField(label="Cómo se verificó la identidad", choices=MetodoVerificacion.choices)
    verificado_en = forms.DateTimeField(label="Cuándo (hora de Costa Rica)",
                                        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
                                        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"])
