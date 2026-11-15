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
    PROPIETARIO_REGISTRAR_DATOS = "propietario:registrar_datos"  # AC·O1b CR/MO

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
