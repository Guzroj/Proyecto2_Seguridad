"""
Genera docs/cobertura-matriz.md: una fila por cada una de las 78 celdas de la
matriz (autorizacion.matriz, con la corrección 3), con sus acciones, dónde se
aplican en el código y qué pruebas las verifican.

    python manage.py tabla_cobertura            # escribe el archivo
    python manage.py tabla_cobertura --stdout   # lo muestra

autorizacion/test_cobertura_tabla.py comprueba que el archivo está al día y que
cada prueba citada existe.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from autorizacion import matriz

ARCHIVO = Path(settings.BASE_DIR) / "docs" / "cobertura-matriz.md"

NOMBRES_OBJETO = {
    "O1a": "Fincas y parcelas", "O1b": "Datos personales del propietario", "O2": "Períodos",
    "O3": "Asignaciones", "O4": "Mediciones", "O5": "Enmiendas", "O6": "Certificados",
    "O7a": "Cuentas, ventanas y sesiones", "O7b": "Roles y solicitudes", "O7c": "Dispositivos de campo",
    "O7d": "Segundo factor", "O8": "Bitácora", "O9": "Alertas",
}

# ---------------------------------------------------------------------------
# Dónde se aplica cada acción del lado Django (PEP): servicio o pantalla que llama al motor.
# ---------------------------------------------------------------------------
P, PC, PM = "cuentas/pantallas.py:", "catalogo/pantallas.py:", "mediciones/pantallas.py:"
PCE, PB, PX = "certificados/pantallas.py:", "bitacora/pantallas.py:", "exportaciones/pantallas.py:"
S_X = "exportaciones/servicios.py:exportar"
S_CAT, S_MED, S_CTA = "catalogo/services.py:", "mediciones/services.py:", "cuentas/services.py:"
LISTAS = "autorizacion/listas.py:listar"

DONDE = {
    "finca:crear": [S_CAT + "crear_finca", PC + "finca_nueva"],
    "finca:modificar": [S_CAT + "modificar_finca", PC + "finca_modificar"],
    "finca:baja": [S_CAT + "dar_baja_finca", PC + "finca_baja"],
    "parcela:crear": [S_CAT + "crear_parcela", PC + "parcela_nueva"],
    "parcela:modificar_geometria": [S_CAT + "modificar_geometria", PC + "parcela_geometria"],
    "parcela:baja": [S_CAT + "dar_baja_parcela", PC + "parcela_baja"],
    "propietario:crear_datos": [S_CAT + "registrar_datos_propietario", PC + "propietario_datos"],
    "propietario:registrar_datos": [S_CAT + "registrar_datos_propietario", PC + "propietario_datos"],
    "periodo:abrir": [S_MED + "abrir_periodo", PM + "periodo_abrir"],
    "periodo:pasar_verificacion": [S_MED + "pasar_a_verificacion", PM + "periodo_pasar"],
    "periodo:cerrar": [S_MED + "cerrar_periodo", PM + "periodo_cerrar"],
    "asignacion:crear": [S_MED + "crear_asignacion", PM + "asignacion_nueva"],
    "asignacion:terminar": [S_MED + "terminar_asignacion", PM + "asignacion_terminar"],
    "medicion:crear": [S_MED + "crear_medicion", PM + "medicion_nueva"],
    "medicion:modificar": [S_MED + "modificar_medicion", PM + "medicion_modificar"],
    "medicion:enviar": [S_MED + "enviar_medicion", PM + "medicion_enviar"],
    "medicion:aprobar": [S_MED + "aprobar_medicion", PM + "medicion_aprobar"],
    "medicion:devolver": [S_MED + "devolver_medicion", PM + "medicion_devolver"],
    "medicion:sincronizar": [S_CTA + "cerrar_baja_tecnico", P + "cuenta_cerrar_baja"],
    "enmienda:solicitar": [S_MED + "solicitar_enmienda", PM + "enmienda_solicitar"],
    "enmienda:autorizar": [S_MED + "autorizar_enmienda", PM + "enmienda_autorizar"],
    "enmienda:rechazar": [S_MED + "rechazar_enmienda", PM + "enmienda_rechazar"],
    # Lecturas y listas de O1–O5 desde las pantallas del flujo de negocio
    "finca:leer": [PC + "finca", PC + "finca_modificar", PC + "finca_baja", PC + "propietario_datos",
                   PC + "parcela_nueva"],
    "parcela:leer": [PC + "parcela", PC + "parcela_geometria", PC + "parcela_baja"],
    "propietario:leer": [PC + "finca", PC + "propietario_datos"],
    "periodo:leer": [PM + "periodo_pasar", PM + "periodo_cerrar"],
    "asignacion:leer": [PM + "asignacion_terminar"],
    "medicion:leer": [PM + "medicion", PM + "medicion_modificar", PM + "medicion_enviar", PM + "medicion_aprobar",
                      PM + "medicion_devolver", PM + "enmienda_solicitar"],
    "enmienda:leer": [PM + "enmienda_autorizar", PM + "enmienda_rechazar"],
    "finca:listar": [LISTAS, PC + "fincas", PM + "asignacion_nueva", PM + "asignaciones"],
    "parcela:listar": [LISTAS, PM + "medicion_nueva"],
    "periodo:listar": [LISTAS, PM + "periodos", PM + "mediciones", PM + "medicion"],
    "asignacion:listar": [LISTAS, PM + "asignaciones"],
    "medicion:listar": [LISTAS, PM + "mediciones"],
    "enmienda:listar": [LISTAS, PM + "enmiendas", PM + "medicion"],
    "cuenta:crear": [S_CTA + "crear_cuenta", P + "cuenta_nueva"],
    "cuenta:modificar": [S_CTA + "modificar_cuenta", S_CTA + "enlace_activacion", P + "cuenta_modificar",
                         P + "cuenta_enlace"],
    "cuenta:baja": [S_CTA + "dar_de_baja", P + "cuenta_baja"],
    "ventana:autorizar": [S_CTA + "aprobar_ventana", P + "ventana_aprobar"],
    "rol:asignar": [S_CTA + "asignar_rol", P + "rol_asignar"], "rol:retirar": [S_CTA + "retirar_rol", P + "rol_retirar"],
    "rol:autorizar": [S_CTA + "autorizar_solicitud", P + "solicitud_autorizar"],
    "rol:rechazar": [S_CTA + "rechazar_solicitud", P + "solicitud_rechazar"],
    "dispositivo:registrar": [S_CTA + "registrar_dispositivo", P + "dispositivos"],
    "dispositivo:revocar": [S_CTA + "revocar_dispositivo", P + "dispositivo_revocar"],
    "factor:registrar": ["cuentas/mfa.py:ExigirMFAMiddleware (rutas de allauth)"],
    "factor:solicitar_restablecimiento": [S_CTA + "solicitar_restablecimiento", P + "restablecimiento_solicitar"],
    "factor:autorizar_restablecimiento": [S_CTA + "autorizar_restablecimiento", P + "restablecimiento_autorizar"],
    "factor:rechazar_restablecimiento": [S_CTA + "rechazar_restablecimiento", P + "restablecimiento_rechazar"],
    "alerta:resolver": [S_CTA + "resolver_alerta", P + "alerta_resolver"],
    # Lecturas de un objeto desde las pantallas propias
    "cuenta:leer": [P + "cuenta", P + "mi_cuenta", P + "ventana_aprobar"],
    "rol:leer": [P + "solicitud_autorizar", P + "solicitud_rechazar"],
    "dispositivo:leer": [P + "dispositivo_revocar"],
    "factor:leer": [P + "restablecimiento_autorizar", P + "restablecimiento_rechazar"],
    "alerta:leer": [P + "alerta_resolver"],
    # Listas: el motor decide la lista y el PEP filtra por el alcance de cada rol
    "cuenta:listar": [LISTAS, P + "cuentas", P + "mi_cuenta", P + "ventanas",
                      PM + "mediciones", PM + "medicion", PM + "asignaciones", PM + "enmiendas"],
    "rol:listar": [LISTAS, P + "solicitudes_rol", P + "cuenta"],
    "dispositivo:listar": [LISTAS, P + "dispositivos"],
    "factor:listar": [LISTAS, P + "restablecimientos"],
    "alerta:listar": [LISTAS, P + "alertas"],
    # O6 · Certificados (Persona 4): lectura y lista; la emisión no es de nadie (SYS-EMI)
    "certificado:leer": [PCE + "certificado", PCE + "certificado_descargar"],
    "certificado:listar": [LISTAS, PCE + "certificados"],
    # O8 · Bitácora (Persona 4): solo lectura
    "bitacora:leer": [PB + "evento"],
    "bitacora:listar": [LISTAS, PB + "eventos", PB + "verificar"],
}
# Columnas sin pantalla todavía (hoy ninguna: O6 y O8 ya tienen las suyas).
PENDIENTE: frozenset = frozenset()


def donde(accion: str) -> list[str]:
    if accion in DONDE:
        return DONDE[accion]
    objeto, operacion = matriz.ACCIONES[accion]
    if accion.endswith(":listar"):
        return [LISTAS]
    lugares = ["autorizacion/cliente.py:autorizar"]
    if operacion == "X":
        lugares += [S_X, PX + "exportar", "cuentas/hbac.py:aplicar (conteo H3) y marca_de_agua"]
    if objeto in PENDIENTE:
        lugares.append("(pantalla pendiente)")
    return lugares


# ---------------------------------------------------------------------------
# Pruebas: archivo::Clase[.método]
# ---------------------------------------------------------------------------
TC, TI1, TI2, TI3 = ("autorizacion/test_condiciones.py::", "autorizacion/test_integracion_o1_o3.py::",
                     "autorizacion/test_integracion_o4_o6.py::", "autorizacion/test_integracion_o7_o9.py::")
TL, TP, TPR, TPI = ("autorizacion/test_listas.py::", "cuentas/test_pantallas.py::",
                    "cuentas/test_pantallas_roles.py::", "cuentas/test_pantallas_identidad.py::")
TD = "mediciones/test_disparadores.py::"  # la base revalida con disparadores (defensa en profundidad)
TCE, TPCE = "certificados/test_certificados.py::", "certificados/test_pantallas.py::"
TB, TX = "bitacora/test_bitacora.py::", "exportaciones/test_exportaciones.py::"

SIEMPRE = ["autorizacion/test_cobertura.py::CeldaPorCeldaTests", "autorizacion/test_reglas.py::MatrizTests"]
VACIA = ["autorizacion/test_reglas.py::DecisionTests.test_rg1_denegacion_por_defecto"]
SIN_CONDICION = ["autorizacion/test_reglas.py::DecisionTests.test_celdas_sin_condicion_conceden"]

POR_CONDICION = {
    "C1": [TC + "C1Tests", TI2 + "O4Tests.test_c1_crear", TI2 + "O4Tests.test_v3_corrige_su_devuelta_con_el_periodo_en_verificacion"],
    "C2": [TC + "C2Tests", TI2 + "O4Tests.test_c2_aprobar_y_v4",
           TD + "MedicionTests.test_c2_con_sujeto_solo_aprueba_el_verificador_asignado"],
    "C3": [TC + "C3Tests", TI2 + "EnmiendaTests.test_v15_solicitar_autorizar_y_aprobar_la_version"],
    "C4": [TC + "C4Tests", TI1 + "LecturasTests.test_c4_pf_ve_lo_suyo", TL + "AlcanceTests.test_pf_solo_ve_lo_suyo",
           TCE + "LecturaC4Tests", TPCE + "CertificadosPantallaTests"],
    "C5": [TC + "C5Tests", TI3 + "VentanaYAuditorTests", TL + "AlcanceTests.test_ae_fuera_de_su_ventana_no_lista",
           TX + "ExportarTests.test_ae_fuera_de_su_ventana_no_exporta"],
    "C6": [TC + "C6C8C10Tests.test_c6_tc_lee_la_finca_que_tiene_asignada",
           TI1 + "LecturasTests.test_c6_y_v1_tc_solo_ve_la_finca_asignada",
           TL + "AlcanceTests.test_tc_ve_lo_asignado_y_lo_suyo"],
    "C7": [TC + "C7Tests", TI3 + "CuentasYRolesTests.test_v10_y_c7",
           TI3 + "CuentasYRolesTests.test_c7_e_se_reevalua_al_autorizar", TPR + "RolesTests"],
    "C8": [TC + "C6C8C10Tests.test_c8_tc_lee_sus_asignaciones", TI1 + "LecturasTests.test_c8_tc_ve_sus_asignaciones"],
    "C9": [TC + "C9Tests", TI1 + "LecturasTests.test_c9_vf_ve_lo_que_verifica", TL + "AlcanceTests.test_vf_ve_lo_que_verifico"],
    "C10": [TC + "C1Tests.test_c10_tc_lee_sus_mediciones", TI2 + "O4Tests.test_c1_modificar_y_enviar_y_c10_leer"],
    "C11": [TC + "C11C12Tests.test_c11_solicitar", TI2 + "EnmiendaTests.test_v15_solicitar_autorizar_y_aprobar_la_version"],
    "C12": [TC + "C11C12Tests.test_c12_leer", TL + "AlcanceTests.test_tc_ve_lo_asignado_y_lo_suyo"],
    "C13": [TC + "C13Tests", TI1 + "EscriturasTests.test_c13_y_v16", TD + "PeriodoTests"],
    "C14": [TC + "C14C15Tests.test_c14", TI3 + "VentanaYAuditorTests.test_c14_y_c5", TPR + "VentanasTests"],
    "C15": [TC + "C14C15Tests.test_c15", TI3 + "CuentasYRolesTests.test_c15", TP + "AltaTests", TP + "CambiosTests"],
    "C16": [TC + "C16Tests", TI2 + "C16Tests", TPI + "CierreBajaTests"],
    "C17": [TC + "C17C18Tests.test_c17_solicitar", TC + "C17C18Tests.test_c17_resolver",
            TC + "C17C18Tests.test_c17_leer_gc_solo_lo_que_podria_resolver", TI3 + "DispositivosFactorYAlertasTests.test_c17",
            TL + "RestablecimientosGCTests", TPI + "RestablecimientoTests"],
    "C18": [TC + "C17C18Tests.test_c18", TI3 + "DispositivosFactorYAlertasTests.test_c18", TPI + "DispositivosTests"],
    "C19": [TC + "C19Tests", TI1 + "EscriturasTests.test_c19_y_v17", TD + "AsignacionTests"],
    "C20": [TC + "C20Tests", TI1 + "EscriturasTests.test_c20_lo_dado_de_baja_no_se_toca", TD + "CatalogoTests",
            "catalogo/test_traslape.py::TraslapeTests"],
    "C21": [TC + "C21C22Tests", TI3 + "DispositivosFactorYAlertasTests.test_c21",
            TP + "FichaTests.test_fuera_de_alcance_es_404_igual_que_inexistente_y_queda_en_bitacora"],
    "C22": [TC + "C21C22Tests.test_c22_y_v14", TI3 + "DispositivosFactorYAlertasTests.test_c22_y_v14", TPI + "AlertasTests"],
    "H1": ["autorizacion/test_hbac.py::H1Tests", "autorizacion/test_integracion_hbac.py::H1Tests"],
    "H2": ["autorizacion/test_hbac.py::H2Tests", "autorizacion/test_integracion_hbac.py::H2Tests"],
    "H3": ["autorizacion/test_hbac.py::H3Tests", "autorizacion/test_integracion_hbac.py::H3Tests",
           TX + "ExportarTests", TPCE + "ExportacionPantallaTests"],
    "H4": [TC + "C1Tests.test_v2_h4_aprobada_o_periodo_cerrado_no_se_modifica_nunca",
           TI2 + "O4Tests.test_v2_h4_una_aprobada_no_se_modifica",
           TD + "MedicionTests.test_h4_una_aprobada_no_cambia_ni_por_sql", TD + "ActoresTests"],
}
RLS = {
    "O1a": ["cuentas/test_rls_dominio.py::LecturaCatalogoTests", "cuentas/test_rls_dominio.py::EscrituraCatalogoTests"],
    "O1b": ["cuentas/test_rls_dominio.py::LecturaCatalogoTests.test_datos_personales_solo_el_propietario_ac_y_ae",
            "cuentas/test_rls_dominio.py::CertificadoTests"],
    "O2": ["cuentas/test_rls_dominio.py::LecturaCatalogoTests.test_periodos_para_los_roles_con_o2",
           "cuentas/test_rls_dominio.py::EscrituraCatalogoTests.test_solo_gc_cambia_periodos"],
    "O3": ["cuentas/test_rls_dominio.py::LecturaCatalogoTests.test_fincas_por_finca_relacionada"],
    "O4": ["cuentas/test_rls_dominio.py::LecturaMedicionesTests", "cuentas/test_rls_dominio.py::EscrituraMedicionesTests"],
    "O5": ["cuentas/test_rls_dominio.py::LecturaMedicionesTests", "cuentas/test_rls_dominio.py::FlujoMedicionesTests"],
    "O7a": ["cuentas/test_rls.py::AutenticadoTests", "cuentas/test_gestion.py::GestionRLSTests",
            "cuentas/test_ventanas.py::VentanasRLSTests", "cuentas/test_sesion.py::SesionRLSTests"],
    "O7b": ["cuentas/test_roles_c7.py::C7RLSTests"],
    "O7c": ["cuentas/test_dispositivos.py::DispositivosRLSTests"],
    "O7d": ["cuentas/test_restablecimiento.py::C17RLSTests", "cuentas/test_mfa.py::PreingresoRLSTests"],
    "O6": [TCE + "RLSCertificadosTests", TCE + "DisparadoresTests", TCE + "EmisionTests", TCE + "P2Tests",
           TCE + "ManipulacionTests", TPCE + "VerificacionPublicaTests"],
    "O8": [TB + "PermisosTests", TB + "CadenaTests", TB + "NegacionesFueraDelRollbackTests", TB + "SellosTests",
           TPCE + "BitacoraPantallaTests"],
    "O9": ["cuentas/test_alertas.py::AlertasRLSTests"],
}
SIN_ACCION_PROPIA: dict[tuple[str, str, str], str] = {}  # hoy ninguna (AC·O1b CR: propietario:crear_datos)


def texto_celda(celda: dict) -> str:
    if not celda:
        return "—"
    partes = []
    for op, p in celda.items():
        etiquetas = [*p.condiciones, *(h for h in p.hbac if h != "H2")]
        partes.append(f"{op} [{', '.join(etiquetas)}]" if etiquetas else op)
        if "H2" in p.hbac:
            partes[-1] += " ‡"
    return "; ".join(partes)


def acciones_de(objeto: str, operacion: str) -> list[str]:
    return sorted(a for a, destino in matriz.ACCIONES.items() if destino == (objeto, operacion))


def _unicos(lista):
    return list(dict.fromkeys(lista))


def fila(numero: int, rol: str, objeto: str) -> str:
    celda = matriz.MATRIZ[rol][objeto]
    if not celda:
        acciones = "—"
        lugares = ["autorizacion/reglas.py:decidir (RG1)"]
        pruebas = SIEMPRE + VACIA
    else:
        lineas, lugares, pruebas = [], ["autorizacion/reglas.py:decidir"], list(SIEMPRE)
        for op, permiso in celda.items():
            nombres = acciones_de(objeto, op)
            if (rol, objeto, op) in SIN_ACCION_PROPIA:
                lineas.append(f"{op}: {SIN_ACCION_PROPIA[(rol, objeto, op)]}")
            else:
                lineas.append(f"{op}: " + ", ".join(f"`{a}`" for a in nombres))
            for a in nombres:
                lugares += donde(a)
            for c in permiso.condiciones:
                lugares.append(f"autorizacion/condiciones.py:{c.lower()}")
                pruebas += POR_CONDICION[c]
            for h in permiso.hbac:
                lugares.append("autorizacion/condiciones.py:h4" if h == "H4" else "autorizacion/hbac.py:controlar")
                if h == "H1":
                    lugares.append("autorizacion/hbac.py:suspension_h1")
                pruebas += POR_CONDICION[h]
            if not permiso.condiciones:
                pruebas += SIN_CONDICION
        acciones = "<br>".join(lineas)
        pruebas += RLS.get(objeto, [])
    codigo = lambda xs: "<br>".join(f"`{x}`" if ".py" in x else x for x in _unicos(xs))  # noqa: E731
    return (f"| {numero} | {rol} | {objeto} · {NOMBRES_OBJETO[objeto]} | {texto_celda(celda)} | {acciones} | "
            f"{codigo(lugares)} | {codigo(pruebas)} |")


def generar() -> str:
    filas, n = [], 0
    for rol in matriz.MATRIZ:
        for objeto in matriz.OBJETOS:
            n += 1
            filas.append(fila(n, rol, objeto))
    return "\n".join([
        "# Cobertura de la matriz de control de acceso",
        "",
        "Una fila por cada una de las 78 celdas de la matriz (6 roles × 13 objetos), con la corrección 3",
        "(GC·O7d L acotada por C17). Para cada celda: las operaciones con sus condiciones, las acciones del",
        "código que las ejercen, dónde se aplican y qué pruebas lo verifican.",
        "",
        "- **Celda**: `—` = denegación explícita; `[Cx, Hy]` = condiciones y controles; `‡` = alerta H2 fuera de horario.",
        "- **Dónde se aplica**: el motor (`autorizacion/reglas.py:decidir` y las condiciones) decide; el punto de",
        "  aplicación del lado Django (PEP) es el servicio o la pantalla que llama a `autorizar()`, o",
        "  `autorizacion/listas.py:listar` para las listas (`<objeto>:listar`).",
        "- **Pruebas**: `archivo::Clase[.método]`. `CeldaPorCeldaTests` revisa en el motor real cada rol con cada",
        f"  acción ({len(matriz.MATRIZ) * len(matriz.ACCIONES)} combinaciones): fuera de la celda niega por RG1;"
        " sin condiciones concede; con condiciones,",
        "  las evalúa. Cada condición tiene además sus pruebas propias y de integración; la última parte de la",
        "  columna son las pruebas de RLS de esa columna (defensa en profundidad).",
        "",
        "Generada con `python manage.py tabla_cobertura`; `autorizacion/test_cobertura_tabla.py` comprueba que",
        "está al día con la matriz y que cada prueba citada existe.",
        "",
        "| # | Rol | Objeto | Celda | Acciones | Dónde se aplica | Pruebas |",
        "|---|---|---|---|---|---|---|",
        *filas,
        "",
        "## Observaciones",
        "",
        "- **AC·O1b CR y MO**: `catalogo/services.py:registrar_datos_propietario` se autoriza como",
        "  `propietario:crear_datos` (CR) si el propietario aún no tiene datos y como `propietario:registrar_datos`",
        "  (MO) si ya los tiene.",
        "- **O1–O5**: las pantallas de `catalogo/pantallas.py` y `mediciones/pantallas.py` llaman al motor en",
        "  cada lectura y lista, y a los servicios en cada cambio.",
        "- **O6**: `certificados/pantallas.py` (lista, ficha y descarga) pasa por el motor; la emisión y la",
        "  revocación no son operaciones de ninguna persona: las hace el proceso SYS-EMI dentro de la aprobación",
        "  (`certificados/servicios.py:emitir`, gancho `on_medicion_aprobada`) y la base las revalida con",
        "  disparadores y RLS. La verificación pública (`certificados/pantallas.py:verificar`) no lee O6: solo",
        "  el estado de un certificado cuyo id y firma coinciden.",
        "- **O8**: `bitacora/pantallas.py` es de solo lectura; nadie escribe la bitácora por una pantalla. Los",
        "  eventos los inserta `bitacora/servicios.py:registrar_evento` y la base los encadena (SHA-256) y",
        "  rechaza UPDATE y DELETE.",
        "- **Exportaciones (X)**: `exportaciones/servicios.py:exportar` cuenta los registros reales, el motor",
        "  aplica C5 y H3 con ese número (`{\"registros\": N}`), Django lo anota (`cuentas/hbac.py:aplicar`) y el",
        "  archivo lleva `cuentas/hbac.py:marca_de_agua`; la exportación y cada negación quedan en O8.",
        "- **factor:registrar (C21)**: el registro de factores es de allauth; `cuentas/mfa.py:ExigirMFAMiddleware`",
        "  exige segundo factor reciente si ya hay uno, y el motor evalúa C21 con el mismo atributo.",
        "",
    ])


class Command(BaseCommand):
    help = "Genera docs/cobertura-matriz.md (tabla de cobertura de la matriz)."

    def add_arguments(self, parser):
        parser.add_argument("--stdout", action="store_true", help="Mostrar en lugar de escribir el archivo.")

    def handle(self, *args, stdout=False, **opciones):
        contenido = generar()
        if stdout:
            self.stdout.write(contenido)
            return
        ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVO.write_text(contenido, encoding="utf-8")
        self.stdout.write(f"Escrito {ARCHIVO.relative_to(settings.BASE_DIR)} (78 celdas).")
