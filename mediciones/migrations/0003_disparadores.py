"""
Disparadores del flujo de mediciones: la base revalida las reglas críticas aunque
alguien se salte los servicios (defensa en profundidad, Arquitectura §2.3 y §4.2).

  mediciones_medicionactor  actores(m) es de solo inserción: no se modifica ni se borra.
  mediciones_medicion       H4 (APROBADA o período CERRADO no cambia), transiciones válidas,
                            identidad inmutable, datos de campo solo en BORRADOR/DEVUELTA,
                            y C2 al aprobar o devolver: el sujeto es el verificador asignado,
                            no es actor de la cadena, no fue técnico de la finca y período,
                            ni es su propietario.
  mediciones_periodo        C13: ABIERTO -> EN_VERIFICACION -> CERRADO, nada sobre uno CERRADO
                            y no se cierra con mediciones pendientes.
  mediciones_enmienda       estados SOLICITADA -> AUTORIZADA | RECHAZADA -> APLICADA | DESCARTADA;
                            lo solicitado no cambia; quien resuelve es el sujeto de la conexión.
  mediciones_asignacion     C19: el técnico no es propietario de la finca, ni en un período
                            CERRADO; "modificar" solo termina la vigencia, una vez.
  todas                     RG1: nada se borra.

C2 y C3 se comprueban contra el sujeto de la conexión (app_sujeto(), que fija
cuentas.rls en cada petición). Sin sujeto (consola, tareas, pruebas de servicio)
esas dos comprobaciones no aplican; todas las demás sí, siempre.
TRUNCATE lo cubren los privilegios: el rol de la aplicación no lo tiene.
Errores con ERRCODE insufficient_privilege, igual que los disparadores de cuentas.
"""

from django.db import migrations

SQL = r"""
-- actores(m): solo inserción --------------------------------------------------
CREATE OR REPLACE FUNCTION mediciones_actor_solo_insercion() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'El historial de actores de una medición es de solo inserción.'
        USING ERRCODE = 'insufficient_privilege';
END
$$;
CREATE TRIGGER medicionactor_solo_insercion BEFORE UPDATE OR DELETE ON mediciones_medicionactor
    FOR EACH ROW EXECUTE FUNCTION mediciones_actor_solo_insercion();

-- Medición ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mediciones_medicion_alta() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    periodo_estado text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM catalogo_parcelaversion v
                   WHERE v.id = NEW.parcela_version_id AND v.parcela_id = NEW.parcela_id) THEN
        RAISE EXCEPTION 'C20: la versión de geometría no es de esa parcela.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.version_de_id IS NULL THEN
        SELECT estado INTO periodo_estado FROM mediciones_periodo WHERE id = NEW.periodo_id;
        IF NEW.estado <> 'BORRADOR' OR periodo_estado IS DISTINCT FROM 'ABIERTO' THEN
            RAISE EXCEPTION 'C1: una medición nueva nace en BORRADOR y en un período ABIERTO.'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSIF NEW.estado <> 'ENVIADA' THEN
        RAISE EXCEPTION 'P3: la versión de una enmienda nace ENVIADA.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER medicion_alta BEFORE INSERT ON mediciones_medicion
    FOR EACH ROW EXECUTE FUNCTION mediciones_medicion_alta();

CREATE OR REPLACE FUNCTION mediciones_medicion_cambio() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    periodo_estado text;
    raiz uuid;
    sujeto bigint := app_sujeto();
    datos_cambian boolean;
    enviando boolean;
    verificando boolean;
BEGIN
    IF NEW.id <> OLD.id OR NEW.parcela_id <> OLD.parcela_id
       OR NEW.parcela_version_id <> OLD.parcela_version_id OR NEW.periodo_id <> OLD.periodo_id
       OR NEW.autor_id <> OLD.autor_id OR NEW.version_de_id IS DISTINCT FROM OLD.version_de_id
       OR NEW.creada_en <> OLD.creada_en THEN
        RAISE EXCEPTION 'La parcela, la geometría, el período, el autor y el origen de una medición no cambian.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- H4: lo aprobado es final; lo de un período CERRADO solo se corrige por enmienda.
    IF OLD.estado = 'APROBADA' THEN
        RAISE EXCEPTION 'H4: una medición APROBADA no se modifica; se corrige por enmienda.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT estado INTO periodo_estado FROM mediciones_periodo WHERE id = OLD.periodo_id;
    IF periodo_estado IS NULL THEN
        RAISE EXCEPTION 'No se pudo leer el período de la medición (fallo cerrado).'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.version_de_id IS NULL AND periodo_estado = 'CERRADO' THEN
        RAISE EXCEPTION 'H4: la medición es de un período CERRADO; se corrige por enmienda.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    enviando := OLD.estado IN ('BORRADOR', 'DEVUELTA') AND NEW.estado = 'ENVIADA';
    verificando := OLD.estado = 'ENVIADA' AND NEW.estado IN ('APROBADA', 'DEVUELTA');
    IF NEW.estado <> OLD.estado AND NOT (enviando OR verificando) THEN
        RAISE EXCEPTION 'Transición de medición no permitida: % -> %.', OLD.estado, NEW.estado
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    datos_cambian := NEW.numero_arboles <> OLD.numero_arboles
        OR NEW.dap_promedio_cm <> OLD.dap_promedio_cm
        OR NEW.altura_promedio_m <> OLD.altura_promedio_m
        OR NEW.observaciones <> OLD.observaciones;
    IF datos_cambian AND (OLD.version_de_id IS NOT NULL OR NEW.estado <> OLD.estado
                          OR OLD.estado NOT IN ('BORRADOR', 'DEVUELTA')) THEN
        RAISE EXCEPTION 'Los datos de campo solo cambian en BORRADOR o DEVUELTA, nunca en una versión enmendada.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.toneladas_co2 IS DISTINCT FROM OLD.toneladas_co2
       AND NOT (OLD.estado IN ('BORRADOR', 'DEVUELTA') OR (OLD.estado = 'ENVIADA' AND NEW.estado = 'APROBADA')) THEN
        RAISE EXCEPTION 'Las toneladas solo las recalcula el servidor al editar o al aprobar.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.verificador_id IS DISTINCT FROM OLD.verificador_id AND NOT enviando THEN
        RAISE EXCEPTION 'C2: el verificador solo lo asigna el sistema al enviar.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.enviada_en IS DISTINCT FROM OLD.enviada_en AND NOT enviando THEN
        RAISE EXCEPTION 'La fecha de envío solo cambia al enviar.' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.aprobada_en IS DISTINCT FROM OLD.aprobada_en AND NOT (verificando AND NEW.estado = 'APROBADA') THEN
        RAISE EXCEPTION 'La fecha de aprobación solo se fija al aprobar.' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (NEW.motivo_devolucion_id IS DISTINCT FROM OLD.motivo_devolucion_id
        OR NEW.comentario_devolucion IS DISTINCT FROM OLD.comentario_devolucion)
       AND NOT (enviando OR (verificando AND NEW.estado = 'DEVUELTA')) THEN
        RAISE EXCEPTION 'El motivo de devolución solo se fija al devolver.' USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- C2: quien aprueba o devuelve (el sujeto de la conexión) es independiente.
    IF verificando AND sujeto IS NOT NULL THEN
        IF sujeto IS DISTINCT FROM OLD.verificador_id THEN
            RAISE EXCEPTION 'C2: solo el verificador asignado aprueba o devuelve.'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        raiz := COALESCE(OLD.version_de_id, OLD.id);
        IF EXISTS (SELECT 1 FROM mediciones_medicionactor a
                   JOIN mediciones_medicion m ON m.id = a.medicion_id
                   WHERE a.usuario_id = sujeto AND (m.id = raiz OR m.version_de_id = raiz)) THEN
            RAISE EXCEPTION 'C2: quien participó en la medición no la aprueba ni la devuelve.'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF EXISTS (SELECT 1 FROM mediciones_asignacion a
                   JOIN catalogo_parcela p ON p.finca_id = a.finca_id
                   WHERE p.id = OLD.parcela_id AND a.periodo_id = OLD.periodo_id AND a.tecnico_id = sujeto)
           OR EXISTS (SELECT 1 FROM catalogo_finca f JOIN catalogo_parcela p ON p.finca_id = f.id
                      WHERE p.id = OLD.parcela_id AND f.propietario_id = sujeto) THEN
            RAISE EXCEPTION 'C2: quien fue técnico de la finca y período, o es su propietario, no verifica.'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER medicion_cambio BEFORE UPDATE ON mediciones_medicion
    FOR EACH ROW EXECUTE FUNCTION mediciones_medicion_cambio();

-- Período (C13) ----------------------------------------------------------------
CREATE OR REPLACE FUNCTION mediciones_periodo_cambio() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.estado = 'CERRADO' THEN
        RAISE EXCEPTION 'C13: un período CERRADO no cambia y nadie lo reabre.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.nombre <> OLD.nombre OR NEW.fecha_inicio <> OLD.fecha_inicio OR NEW.fecha_fin <> OLD.fecha_fin
       OR NEW.abierto_por_id <> OLD.abierto_por_id OR NEW.abierto_en <> OLD.abierto_en THEN
        RAISE EXCEPTION 'El nombre, las fechas y la apertura de un período no cambian.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.estado <> OLD.estado AND NOT (
        (OLD.estado = 'ABIERTO' AND NEW.estado = 'EN_VERIFICACION')
        OR (OLD.estado = 'EN_VERIFICACION' AND NEW.estado = 'CERRADO')) THEN
        RAISE EXCEPTION 'C13: transición de período no permitida: % -> %.', OLD.estado, NEW.estado
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.estado = 'CERRADO' AND EXISTS (
        SELECT 1 FROM mediciones_medicion m WHERE m.periodo_id = NEW.id
          AND (m.estado = 'ENVIADA' OR (m.estado = 'DEVUELTA' AND m.version_de_id IS NULL))) THEN
        RAISE EXCEPTION 'C13: no se cierra un período con mediciones ENVIADA o DEVUELTA.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER periodo_cambio BEFORE UPDATE ON mediciones_periodo
    FOR EACH ROW EXECUTE FUNCTION mediciones_periodo_cambio();

-- Enmienda -------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mediciones_enmienda_cambio() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    resolviendo boolean := OLD.estado = 'SOLICITADA' AND NEW.estado IN ('AUTORIZADA', 'RECHAZADA');
BEGIN
    IF OLD.estado IN ('RECHAZADA', 'APLICADA', 'DESCARTADA') THEN
        RAISE EXCEPTION 'Una enmienda % es final.', OLD.estado USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.medicion_original_id <> OLD.medicion_original_id OR NEW.solicitante_id <> OLD.solicitante_id
       OR NEW.justificacion <> OLD.justificacion OR NEW.numero_arboles <> OLD.numero_arboles
       OR NEW.dap_promedio_cm <> OLD.dap_promedio_cm OR NEW.altura_promedio_m <> OLD.altura_promedio_m
       OR NEW.observaciones <> OLD.observaciones OR NEW.solicitada_en <> OLD.solicitada_en THEN
        RAISE EXCEPTION 'Lo que solicitó el técnico en una enmienda no cambia.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.estado <> OLD.estado AND NOT (resolviendo
        OR (OLD.estado = 'AUTORIZADA' AND NEW.estado IN ('APLICADA', 'DESCARTADA'))) THEN
        RAISE EXCEPTION 'Transición de enmienda no permitida: % -> %.', OLD.estado, NEW.estado
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT resolviendo AND (NEW.autorizador_id IS DISTINCT FROM OLD.autorizador_id
        OR NEW.resuelta_en IS DISTINCT FROM OLD.resuelta_en OR NEW.version_id IS DISTINCT FROM OLD.version_id
        OR NEW.motivo_rechazo IS DISTINCT FROM OLD.motivo_rechazo) THEN
        RAISE EXCEPTION 'Quién resolvió una enmienda, cuándo y su versión no cambian.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- C3: quien resuelve es el sujeto de la conexión (el doble control ya lo exige un CHECK).
    IF resolviendo AND app_sujeto() IS NOT NULL AND NEW.autorizador_id IS DISTINCT FROM app_sujeto() THEN
        RAISE EXCEPTION 'C3: la enmienda la resuelve quien está conectado, no otra cuenta.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER enmienda_cambio BEFORE UPDATE ON mediciones_enmienda
    FOR EACH ROW EXECUTE FUNCTION mediciones_enmienda_cambio();

-- Asignación (C19) -------------------------------------------------------------
CREATE OR REPLACE FUNCTION mediciones_asignacion_alta() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM catalogo_finca f WHERE f.id = NEW.finca_id AND f.propietario_id = NEW.tecnico_id) THEN
        RAISE EXCEPTION 'C19: nadie mide una finca de la que es propietario.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF EXISTS (SELECT 1 FROM mediciones_periodo p WHERE p.id = NEW.periodo_id AND p.estado = 'CERRADO') THEN
        RAISE EXCEPTION 'C19: no se asigna en un período CERRADO.' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER asignacion_alta BEFORE INSERT ON mediciones_asignacion
    FOR EACH ROW EXECUTE FUNCTION mediciones_asignacion_alta();

CREATE OR REPLACE FUNCTION mediciones_asignacion_cambio() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.vigente_hasta IS NOT NULL OR NEW.tecnico_id <> OLD.tecnico_id OR NEW.finca_id <> OLD.finca_id
       OR NEW.periodo_id <> OLD.periodo_id OR NEW.vigente_desde <> OLD.vigente_desde
       OR NEW.creada_por_id <> OLD.creada_por_id THEN
        RAISE EXCEPTION 'C19: una asignación solo se termina, una vez; su historial no cambia.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER asignacion_cambio BEFORE UPDATE ON mediciones_asignacion
    FOR EACH ROW EXECUTE FUNCTION mediciones_asignacion_cambio();

-- RG1: nada se borra -----------------------------------------------------------
CREATE TRIGGER medicion_sin_borrado BEFORE DELETE ON mediciones_medicion
    FOR EACH ROW EXECUTE FUNCTION dominio_sin_borrado();
CREATE TRIGGER periodo_sin_borrado BEFORE DELETE ON mediciones_periodo
    FOR EACH ROW EXECUTE FUNCTION dominio_sin_borrado();
CREATE TRIGGER enmienda_sin_borrado BEFORE DELETE ON mediciones_enmienda
    FOR EACH ROW EXECUTE FUNCTION dominio_sin_borrado();
CREATE TRIGGER asignacion_sin_borrado BEFORE DELETE ON mediciones_asignacion
    FOR EACH ROW EXECUTE FUNCTION dominio_sin_borrado();
CREATE TRIGGER motivodevolucion_sin_borrado BEFORE DELETE ON mediciones_motivodevolucion
    FOR EACH ROW EXECUTE FUNCTION dominio_sin_borrado();
"""

REVERSO = r"""
DROP TRIGGER IF EXISTS motivodevolucion_sin_borrado ON mediciones_motivodevolucion;
DROP TRIGGER IF EXISTS asignacion_sin_borrado ON mediciones_asignacion;
DROP TRIGGER IF EXISTS enmienda_sin_borrado ON mediciones_enmienda;
DROP TRIGGER IF EXISTS periodo_sin_borrado ON mediciones_periodo;
DROP TRIGGER IF EXISTS medicion_sin_borrado ON mediciones_medicion;
DROP TRIGGER IF EXISTS asignacion_cambio ON mediciones_asignacion;
DROP TRIGGER IF EXISTS asignacion_alta ON mediciones_asignacion;
DROP TRIGGER IF EXISTS enmienda_cambio ON mediciones_enmienda;
DROP TRIGGER IF EXISTS periodo_cambio ON mediciones_periodo;
DROP TRIGGER IF EXISTS medicion_cambio ON mediciones_medicion;
DROP TRIGGER IF EXISTS medicion_alta ON mediciones_medicion;
DROP TRIGGER IF EXISTS medicionactor_solo_insercion ON mediciones_medicionactor;
DROP FUNCTION IF EXISTS mediciones_asignacion_cambio();
DROP FUNCTION IF EXISTS mediciones_asignacion_alta();
DROP FUNCTION IF EXISTS mediciones_enmienda_cambio();
DROP FUNCTION IF EXISTS mediciones_periodo_cambio();
DROP FUNCTION IF EXISTS mediciones_medicion_cambio();
DROP FUNCTION IF EXISTS mediciones_medicion_alta();
DROP FUNCTION IF EXISTS mediciones_actor_solo_insercion();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("mediciones", "0002_motivos_devolucion"),
        ("catalogo", "0003_disparadores"),       # dominio_sin_borrado()
        ("cuentas", "0023_rls_mediciones_enmiendas"),  # app_sujeto()
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)]
