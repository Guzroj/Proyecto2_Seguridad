"""
Vías de proceso: lecturas que un proceso del sistema necesita y que el sujeto de
la petición no puede hacer por sí mismo según la matriz.

datos_propietario_para_certificado(medicion)
    La emisión del certificado corre dentro de la aprobación del VF
    (ganchos.on_medicion_aprobada) y necesita el nombre y la identificación del
    propietario, que son O1b y el VF no tiene. Devuelve solo esos dos campos, y
    solo si la medición está APROBADA y el sujeto de la conexión es su
    verificador: lo resuelve certificado_datos_propietario (SECURITY DEFINER),
    así que nadie más obtiene O1b por esta vía. Si no se cumple, FueraDeAlcance.
"""

from django.db import connection

from nucleo.errores import FueraDeAlcance


def _sujeto_de_la_conexion() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.usuario_id', true)")
        return cursor.fetchone()[0] or ""


def datos_propietario_para_certificado(medicion) -> dict:
    medicion_id = getattr(medicion, "pk", medicion)
    if connection.vendor == "postgresql" and _sujeto_de_la_conexion():
        with connection.cursor() as cursor:
            cursor.execute("SELECT nombre_completo, identificacion FROM certificado_datos_propietario(%s)",
                           [str(medicion_id)])
            fila = cursor.fetchone()
        if fila is None:
            raise FueraDeAlcance("No encontrado.")
        return {"nombre_completo": fila[0], "identificacion": fila[1]}

    # Sin sujeto en la conexión: consola o pruebas con el dueño de la base, que ya lee
    # todo. Con app_runtime y sin sujeto, RLS no deja leer nada y termina en 404.
    from catalogo.models import DatosPropietario
    from mediciones.models import EstadoMedicion, Medicion

    m = Medicion.objects.select_related("parcela__finca").filter(pk=medicion_id).first()
    if m is None or m.estado != EstadoMedicion.APROBADA:
        raise FueraDeAlcance("No encontrado.")
    datos = DatosPropietario.objects.filter(usuario_id=m.parcela.finca.propietario_id).values(
        "nombre_completo", "identificacion").first()
    if datos is None:
        raise FueraDeAlcance("No encontrado.")
    return datos
