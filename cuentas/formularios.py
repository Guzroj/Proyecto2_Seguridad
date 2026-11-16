"""
Formularios de las pantallas de cuentas: solo dan forma a la entrada (tipos y
largos). Las reglas las vuelven a aplicar los servicios, que son la única vía
para cambiar datos.
"""

from django import forms

from autorizacion import roles

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
