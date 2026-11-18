"""
Pantallas de certificados (O6).

  certificados          GC·O6 L, AE·O6 L [C5], PF·O6 L [C4]: lista filtrada por el motor
  certificado           ficha: estado, historial de revocación y verificación del documento
  certificado_descargar el archivo firmado (JSON), con la misma autorización que la ficha
  verificar             PÚBLICA: cualquiera sube un archivo y obtiene si es auténtico. No lee
                        la base salvo el estado del certificado cuyo id Y firma coinciden
                        (certificado_estado_publico): no revela nada que no esté en el archivo.
                        Con límite de tasa por IP y por instancia (429): la página es anónima y
                        cada verificación escribe en la bitácora.
  clave_publica         PÚBLICA: huellas de las claves de Verdeazul (ancla de confianza).

Las vistas no deciden permisos: cada lectura pasa por el motor (RG3: fuera de alcance e
inexistente dan el mismo 404). Ninguna pantalla emite, revoca ni modifica certificados:
eso solo lo hace la aprobación de una medición (SYS-EMI).
"""

import json
import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from autorizacion.listas import listar
from bitacora.servicios import ip_de
from cuentas.pantallas import autorizar_lectura
from nucleo import ganchos
from nucleo.acciones import Accion
from nucleo.errores import FueraDeAlcance

from . import firma, formato
from .models import Certificado, EstadoCertificado


def _sin_cache(respuesta):
    respuesta["Cache-Control"] = "no-store"
    return respuesta


def _leer(usuario, pk) -> Certificado:
    try:
        certificado = Certificado.objects.filter(pk=pk).first()
    except (ValueError, TypeError):
        certificado = None
    if certificado is None:
        raise FueraDeAlcance("No encontrado.")
    autorizar_lectura(usuario, Accion.CERTIFICADO_LEER, certificado, "certificado")
    return certificado


def _resumen(c: Certificado) -> dict:
    """Lo que se muestra sale del propio documento firmado: sin JOIN a filas que RLS ocultaría (D19)."""
    contenido = c.contenido_dict
    return {"c": c, "finca": contenido.get("finca", {}), "parcela": contenido.get("parcela", {}),
            "periodo": contenido.get("periodo", {})}


@login_required
def certificados(request):
    lista = listar(request.user, "certificados")
    estado = request.GET.get("estado")
    if estado in EstadoCertificado.values:  # filtro de lista blanca
        lista = lista.filter(estado=estado)
    return _sin_cache(render(request, "certificados/certificados.html", {
        "filas": [_resumen(c) for c in lista.order_by("-emitido_en")[:500]],
        "estados": EstadoCertificado.choices,
        "estado": estado,
    }))


def _visible(usuario, pk):
    """Id de un certificado relacionado solo si el sujeto también puede leerlo (si no, no se muestra)."""
    if pk is None:
        return None
    return listar(usuario, "certificados").filter(pk=pk).values_list("pk", flat=True).first()


@login_required
def certificado(request, pk):
    c = _leer(request.user, pk)
    reemplazado_por = Certificado.objects.filter(reemplaza_a_id=c.pk).values_list("pk", flat=True).first()
    verificacion = formato.verificar(c.documento(), firma.huellas_confiables())
    return _sin_cache(render(request, "certificados/certificado.html", {
        **_resumen(c),
        "contenido": c.contenido_dict,
        "reemplaza_a": _visible(request.user, c.reemplaza_a_id),
        "reemplazado_por": _visible(request.user, reemplazado_por),
        "verificacion": verificacion,
        "huella_legible": formato.huella_legible(c.huella_clave),
    }))


@login_required
@require_GET
def certificado_descargar(request, pk):
    c = _leer(request.user, pk)
    respuesta = HttpResponse(c.archivo(), content_type="application/json; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="certificado-{c.pk}.json"'
    respuesta["X-Content-Type-Options"] = "nosniff"
    return _sin_cache(respuesta)


# ---------------------------------------------------------------------------
# Públicas
# ---------------------------------------------------------------------------
def _estado_registrado(contenido: dict, valor_firma: str) -> dict | None:
    """Estado en el registro de Verdeazul de un certificado íntegro (revocado o vigente)."""
    certificado_id = (contenido.get("certificado") or {}).get("id")
    if connection.vendor != "postgresql" or not isinstance(certificado_id, str):
        return None
    try:
        import uuid

        uuid.UUID(certificado_id)
    except ValueError:
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT estado, revocado_en, reemplazado_por FROM certificado_estado_publico(%s, %s)",
                       [certificado_id, valor_firma])
        fila = cursor.fetchone()
    if fila is None:
        return {"estado": "DESCONOCIDO"}
    return {"estado": fila[0], "revocado_en": fila[1], "reemplazado_por": fila[2]}


# (máximo, segundos): por IP por minuto, por IP por hora y total de la instancia por minuto.
LIMITES_VERIFICACION = {"ip_minuto": (10, 60), "ip_hora": (60, 3600), "instancia_minuto": (300, 60)}


def _excede_limite(ip: str) -> str | None:
    """
    Ventana fija en la caché (por instancia de Cloud Run: con --max-instances=5 el límite real es
    a lo sumo 5 veces este). Devuelve el nombre del límite superado, o None. La caché que falla
    no bloquea la verificación (disponibilidad), pero se deja rastro.
    """
    limites = getattr(settings, "CERTIFICADO_VERIFICACION_LIMITES", LIMITES_VERIFICACION)
    ahora = int(time.time())
    for nombre, (maximo, ventana) in limites.items():
        sujeto = "instancia" if nombre.startswith("instancia") else (ip or "sin-ip")
        clave = f"vz:verificar:{nombre}:{sujeto}:{ahora // ventana}"
        try:
            cache.add(clave, 0, ventana + 5)
            if cache.incr(clave) > maximo:
                return nombre
        except ValueError:
            continue
    return None


def _demasiadas(request, limite: str):
    ventana = getattr(settings, "CERTIFICADO_VERIFICACION_LIMITES", LIMITES_VERIFICACION)[limite][1]
    clave = f"vz:verificar:avisado:{limite}:{ip_de(request) or 'sin-ip'}:{int(time.time()) // ventana}"
    if cache.add(clave, 1, ventana + 5):  # una sola entrada en la bitácora por ventana, no una por intento
        ganchos.registrar_evento(actor=request.user if request.user.is_authenticated else None,
                                 accion=Accion.CERTIFICADO_VERIFICAR, recurso_tipo="certificado", recurso_id="",
                                 resultado="DENEGADO", detalle=f"límite de tasa superado ({limite})")
    respuesta = render(request, "certificados/verificar.html", {
        "limitado": True, "huellas": sorted(firma.huellas_confiables())}, status=429)
    respuesta["Retry-After"] = str(ventana)
    return _sin_cache(respuesta)


@require_http_methods(["GET", "POST"])
def verificar(request):
    """Verificación pública: sube el archivo (o pega su texto) y responde si es auténtico."""
    resultado, registro = None, None
    if request.method == "POST":
        limite = _excede_limite(ip_de(request))
        if limite is not None:
            return _demasiadas(request, limite)
        archivo = request.FILES.get("archivo")
        if archivo is not None:
            datos = archivo.read(formato.TAMANO_MAXIMO + 1)
        else:
            datos = (request.POST.get("texto") or "").encode("utf-8")
        resultado = formato.verificar(datos, firma.huellas_confiables())
        if resultado["valido"]:
            documento = formato.leer(datos)
            registro = _estado_registrado(resultado["contenido"], documento["firma"]["valor"])
        usuario = request.user if request.user.is_authenticated else None
        contenido = resultado["contenido"] or {}
        ganchos.registrar_evento(
            actor=usuario, accion=Accion.CERTIFICADO_VERIFICAR, recurso_tipo="certificado",
            recurso_id=str((contenido.get("certificado") or {}).get("id") or "")[:64],
            resultado="OK",
            despues={"valido": resultado["valido"], "integro": resultado["integro"],
                     "confiable": resultado["confiable"], "registro": (registro or {}).get("estado")},
            detalle="verificación pública de un documento",
        )
    estado = 200 if resultado is None or resultado["valido"] else 422
    return _sin_cache(render(request, "certificados/verificar.html", {
        "resultado": resultado,
        "registro": registro,
        "huellas": sorted(firma.huellas_confiables()),
        "contenido_json": json.dumps(resultado["contenido"], ensure_ascii=False, indent=2, sort_keys=True)
        if resultado and resultado["contenido"] else "",
    }, status=estado))


@require_GET
def clave_publica(request):
    """Ancla de confianza: las huellas que Verdeazul reconoce, servidas por HTTPS desde su dominio."""
    try:
        actual = {"huella": firma.huella_actual(), "clave_publica": firma.clave_publica_actual()}
    except firma.ClaveNoDisponible:
        actual = None
    return JsonResponse({
        "emisor": formato.EMISOR,
        "formato": formato.FORMATO,
        "algoritmo": formato.ALGORITMO,
        "clave_actual": actual,
        "huellas_confiables": sorted(firma.huellas_confiables()),
        "como_verificar": "python certificados/formato.py certificado.json --huella <huella>",
    }, json_dumps_params={"ensure_ascii": False, "indent": 2})
