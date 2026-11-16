"""
Formularios del flujo de mediciones: solo dan forma a la entrada (tipos, largos,
listas cerradas). Las reglas las vuelven a aplicar los servicios.
"""

from django import forms

from autorizacion.listas import listar
from catalogo.models import EstadoCatalogo
from nucleo import ganchos

from .models import EstadoPeriodo, MotivoDevolucion, Periodo


class PeriodoForm(forms.Form):
    nombre = forms.CharField(label="Nombre", max_length=100)
    fecha_inicio = forms.DateField(label="Inicio", widget=forms.DateInput(attrs={"type": "date"}))
    fecha_fin = forms.DateField(label="Fin", widget=forms.DateInput(attrs={"type": "date"}))


class AsignacionForm(forms.Form):
    tecnico = forms.ModelChoiceField(label="Técnico (TC)", queryset=None)
    finca = forms.ModelChoiceField(label="Finca", queryset=None)
    periodo = forms.ModelChoiceField(label="Período", queryset=None)

    def __init__(self, usuario, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tecnico"].queryset = ganchos.usuarios_con_rol("TC").order_by("username")
        self.fields["finca"].queryset = listar(usuario, "fincas").filter(estado=EstadoCatalogo.ACTIVA).order_by("nombre")
        self.fields["periodo"].queryset = Periodo.objects.exclude(estado=EstadoPeriodo.CERRADO)


class DatosCampoForm(forms.Form):
    numero_arboles = forms.IntegerField(label="Número de árboles", min_value=1, max_value=1_000_000)
    dap_promedio_cm = forms.DecimalField(label="Diámetro promedio a la altura del pecho (cm)",
                                         max_digits=6, decimal_places=2, min_value=0)
    altura_promedio_m = forms.DecimalField(label="Altura promedio (m)", max_digits=5, decimal_places=2, min_value=0)
    observaciones = forms.CharField(label="Observaciones", max_length=5000, required=False,
                                    widget=forms.Textarea(attrs={"rows": 3}))


class MedicionNuevaForm(DatosCampoForm):
    """El técnico solo ve las parcelas de sus fincas asignadas (C6) y los períodos ABIERTOS."""

    parcela = forms.ModelChoiceField(label="Parcela", queryset=None)
    periodo = forms.ModelChoiceField(label="Período", queryset=None)
    field_order = ["parcela", "periodo", "numero_arboles", "dap_promedio_cm", "altura_promedio_m", "observaciones"]

    def __init__(self, usuario, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parcela"].queryset = (
            listar(usuario, "parcelas").filter(estado=EstadoCatalogo.ACTIVA)
            .select_related("finca").order_by("finca__nombre", "codigo"))
        self.fields["parcela"].label_from_instance = lambda p: f"{p.finca.nombre} / {p.codigo}"
        self.fields["periodo"].queryset = Periodo.objects.filter(estado=EstadoPeriodo.ABIERTO)


class DevolverForm(forms.Form):
    motivo = forms.ModelChoiceField(label="Motivo (catálogo)", queryset=MotivoDevolucion.objects.filter(activo=True))
    comentario = forms.CharField(label="Comentario", max_length=2000, required=False,
                                 widget=forms.Textarea(attrs={"rows": 3}))


class EnmiendaForm(DatosCampoForm):
    justificacion = forms.CharField(label="Justificación", max_length=5000, widget=forms.Textarea(attrs={"rows": 4}))
    field_order = ["justificacion", "numero_arboles", "dap_promedio_cm", "altura_promedio_m", "observaciones"]


class MotivoRechazoForm(forms.Form):
    motivo = forms.CharField(label="Motivo del rechazo", max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))
