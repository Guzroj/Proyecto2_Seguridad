"""
Vocabulario ÚNICO de acciones. Lo usan:
  - los servicios, al llamar ganchos.autorizar(usuario, accion, recurso, contexto)
  - el motor de autorización de Persona 3, para mapear cada acción a su celda de la matriz
  - la bitácora de Persona 4, como campo "accion" de cada evento

Formato: "<objeto>:<operación>". Entre paréntesis, la celda y operación de la matriz.
"""


class Accion:
    # O1a · Fincas y parcelas
    FINCA_CREAR = "finca:crear"                  # AC·O1a CR (C20)
    FINCA_MODIFICAR = "finca:modificar"          # AC·O1a MO
    FINCA_BAJA = "finca:baja"                    # AC·O1a BA
    PARCELA_CREAR = "parcela:crear"              # AC·O1a CR
    PARCELA_MODIFICAR_GEOMETRIA = "parcela:modificar_geometria"  # AC·O1a MO (C20: versiona)
    PARCELA_BAJA = "parcela:baja"                # AC·O1a BA

    # O1b · Datos personales del propietario
    PROPIETARIO_CREAR_DATOS = "propietario:crear_datos"          # AC·O1b CR (C20): primera vez
    PROPIETARIO_REGISTRAR_DATOS = "propietario:registrar_datos"  # AC·O1b MO (C20): cambio de datos existentes

    # O2 · Períodos
    PERIODO_ABRIR = "periodo:abrir"              # GC·O2 AB (C13)
    PERIODO_PASAR_VERIFICACION = "periodo:pasar_verificacion"  # GC·O2 TV (C13)
    PERIODO_CERRAR = "periodo:cerrar"            # GC·O2 CI (C13)

    # O3 · Asignaciones
    ASIGNACION_CREAR = "asignacion:crear"        # AC·O3 CR (C19)
    ASIGNACION_TERMINAR = "asignacion:terminar"  # AC·O3 MO (C19: solo termina vigencia)

    # O4 · Mediciones
    MEDICION_CREAR = "medicion:crear"            # TC·O4 CR (C1)
    MEDICION_MODIFICAR = "medicion:modificar"    # TC·O4 MO (C1, H4)
    MEDICION_ENVIAR = "medicion:enviar"          # TC·O4 EV (C1)
    MEDICION_APROBAR = "medicion:aprobar"        # VF·O4 AP (C2) -> dispara emisión
    MEDICION_DEVOLVER = "medicion:devolver"      # VF·O4 DV (C2)

    # O5 · Enmiendas
    ENMIENDA_SOLICITAR = "enmienda:solicitar"    # TC·O5 SO (C11)
    ENMIENDA_AUTORIZAR = "enmienda:autorizar"    # GC·O5 AU (C3)
    ENMIENDA_RECHAZAR = "enmienda:rechazar"      # GC·O5 RE (C3)

    # O7a · Cuentas
    CUENTA_CREAR = "cuenta:crear"                # AC·O7a CR (C15)
    CUENTA_MODIFICAR = "cuenta:modificar"        # AC·O7a MO (C15)
    CUENTA_BAJA = "cuenta:baja"                  # AC·O7a BA (C15)

    # O7b · Roles
    ROL_ASIGNAR = "rol:asignar"                  # AC·O7b AR (C7)
    ROL_RETIRAR = "rol:retirar"                  # AC·O7b RR (C7)
    ROL_AUTORIZAR = "rol:autorizar"              # GC·O7b AU (C7: GC/AE)
    ROL_RECHAZAR = "rol:rechazar"                # GC·O7b RE (C7: GC/AE)

    # O7a · Ventanas de auditoría
    VENTANA_AUTORIZAR = "ventana:autorizar"      # GC·O7a AU (C14)
    CUENTA_ACTIVAR = "cuenta:activar"            # tarea diaria: la cuenta AE se activa en v.inicio (C14)

    # O7c · Dispositivos de campo
    DISPOSITIVO_REGISTRAR = "dispositivo:registrar"  # AC·O7c CR (C18)
    DISPOSITIVO_REVOCAR = "dispositivo:revocar"      # AC·O7c RV (sin condición)

    # O7d · Segundo factor
    FACTOR_SOLICITAR_RESTABLECIMIENTO = "factor:solicitar_restablecimiento"    # AC·O7d SO (C17)
    FACTOR_AUTORIZAR_RESTABLECIMIENTO = "factor:autorizar_restablecimiento"    # GC·O7d AU (C17)
    FACTOR_RECHAZAR_RESTABLECIMIENTO = "factor:rechazar_restablecimiento"      # GC·O7d RE (C17)

    # O6 · Certificados: solo el proceso de emisión (SYS-EMI) los crea o revoca, dentro de la
    # aprobación de una medición (P2). Ninguna persona tiene estas operaciones en la matriz.
    CERTIFICADO_EMITIR = "certificado:emitir"        # SYS-EMI: emite O6 al aprobar (C2)
    CERTIFICADO_REVOCAR = "certificado:revocar"      # SYS-EMI: P2, enmienda que cambia las toneladas
    CERTIFICADO_MANTENER = "certificado:mantener"    # SYS-EMI: P2, enmienda sin cambio de toneladas
    CERTIFICADO_VERIFICAR = "certificado:verificar"  # verificación pública de un documento (sin datos de la base)
    BITACORA_SELLAR = "bitacora:sellar"              # proceso horario: firma la cabeza de la cadena (§4.5)

    # O9 · Alertas
    ALERTA_CREAR = "alerta:crear"                # Motor HBAC (proceso): crea en O9
    ALERTA_RESOLVER = "alerta:resolver"          # GC·O9 RS (C22)

    # Lecturas (L) y exportaciones (X) de cada columna, para el PEP de las vistas
    FINCA_LEER = "finca:leer"                    # O1a L
    PARCELA_LEER = "parcela:leer"                # O1a L
    PROPIETARIO_LEER = "propietario:leer"        # O1b L
    PERIODO_LEER = "periodo:leer"                # O2 L
    ASIGNACION_LEER = "asignacion:leer"          # O3 L
    MEDICION_LEER = "medicion:leer"              # O4 L
    ENMIENDA_LEER = "enmienda:leer"              # O5 L
    CERTIFICADO_LEER = "certificado:leer"        # O6 L
    CUENTA_LEER = "cuenta:leer"                  # O7a L
    ROL_LEER = "rol:leer"                        # O7b L
    DISPOSITIVO_LEER = "dispositivo:leer"        # O7c L
    FACTOR_LEER = "factor:leer"                  # O7d L (metadatos)
    BITACORA_LEER = "bitacora:leer"              # O8 L
    ALERTA_LEER = "alerta:leer"                  # O9 L
    FINCA_EXPORTAR = "finca:exportar"            # O1a X (AE, C5, H3)
    PARCELA_EXPORTAR = "parcela:exportar"        # O1a X
    PROPIETARIO_EXPORTAR = "propietario:exportar"  # O1b X
    PERIODO_EXPORTAR = "periodo:exportar"        # O2 X
    ASIGNACION_EXPORTAR = "asignacion:exportar"  # O3 X
    MEDICION_EXPORTAR = "medicion:exportar"      # O4 X
    ENMIENDA_EXPORTAR = "enmienda:exportar"      # O5 X
    CERTIFICADO_EXPORTAR = "certificado:exportar"  # O6 X
    CUENTA_EXPORTAR = "cuenta:exportar"          # O7a X
    ROL_EXPORTAR = "rol:exportar"                # O7b X
    DISPOSITIVO_EXPORTAR = "dispositivo:exportar"  # O7c X
    FACTOR_EXPORTAR = "factor:exportar"          # O7d X
    BITACORA_EXPORTAR = "bitacora:exportar"      # O8 X
    ALERTA_EXPORTAR = "alerta:exportar"          # O9 X

    # Listas (L sobre una lista): autorizacion.listas filtra por el alcance de cada rol
    FINCA_LISTAR = "finca:listar"                # O1a L (lista)
    PARCELA_LISTAR = "parcela:listar"            # O1a L (lista)
    PROPIETARIO_LISTAR = "propietario:listar"    # O1b L (lista)
    PERIODO_LISTAR = "periodo:listar"            # O2 L (lista)
    ASIGNACION_LISTAR = "asignacion:listar"      # O3 L (lista)
    MEDICION_LISTAR = "medicion:listar"          # O4 L (lista)
    ENMIENDA_LISTAR = "enmienda:listar"          # O5 L (lista)
    CERTIFICADO_LISTAR = "certificado:listar"    # O6 L (lista)
    CUENTA_LISTAR = "cuenta:listar"              # O7a L (lista)
    ROL_LISTAR = "rol:listar"                    # O7b L (lista)
    DISPOSITIVO_LISTAR = "dispositivo:listar"    # O7c L (lista)
    FACTOR_LISTAR = "factor:listar"              # O7d L (lista)
    BITACORA_LISTAR = "bitacora:listar"          # O8 L (lista)
    ALERTA_LISTAR = "alerta:listar"              # O9 L (lista)

    # Otras celdas que aún no tenían acción
    FACTOR_REGISTRAR = "factor:registrar"        # Todos·O7d CR (C21)
    MEDICION_SINCRONIZAR = "medicion:sincronizar"  # GC·O4 SI (C16)
