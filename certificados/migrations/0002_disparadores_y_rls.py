"""
Certificados (O6): la base revalida P2 y la emisión aunque alguien se salte el código
(defensa en profundidad, Arquitectura §2.3), con RLS con FORCE.

Disparadores (aplican a cualquiera que escriba, incluido el dueño de la tabla):
  alta      nace EMITIDO, sin datos de revocación, de una medición APROBADA con la misma
            parcela, período, versión de geometría y toneladas; propietario = dueño de la
            finca; el documento firmado declara el mismo id y las mismas toneladas; si hay
            sujeto en la conexión, es el verificador de esa medición (C2). Si reemplaza a
            otro, ese ya está REVOCADO por esta misma medición.
  cambio    solo EMITIDO -> REVOCADO, una vez, por una versión enmendada APROBADA de la misma
            parcela y período cuyas toneladas difieren (P2), aprobada por el sujeto; ningún
            otro campo cambia.
  borrado   nunca (RG1). TRUNCATE lo cubren los privilegios: app_runtime no lo tiene.
El índice único parcial certificado_vigente (migración 0001) impide dos EMITIDO por
parcela y período.

RLS:
  lectura   GC y AE todo (GC·O6 L, AE·O6 L [C5]: C5 la decide el motor); el propietario
            sus certificados EMITIDO o REVOCADO (C4). Nunca en preingreso.
  emisión   fase 'emision' (la fija certificados.servicios solo dentro de la aprobación):
            el VF que aprobó una medición de esa parcela y período lee, inserta y revoca.
  interna   certificado_estado_publico() (SECURITY DEFINER, fase 'verificacion') solo
            devuelve el estado de un certificado cuyo id Y firma coinciden: la verificación
            pública no revela nada que no esté ya en el documento.
"""

from django.db import migrations

NO_PREINGRESO = "app_fase() IS DISTINCT FROM 'preingreso'"
VF_DE_LA_PARCELA = """(app_fase() = 'emision' AND app_tiene_rol('VF') AND EXISTS (
        SELECT 1 FROM mediciones_medicion m
         WHERE m.parcela_id = certificados_certificado.parcela_id
           AND m.periodo_id = certificados_certificado.periodo_id
           AND m.estado = 'APROBADA' AND m.verificador_id = app_sujeto()))"""

SQL = rf"""
CREATE OR REPLACE FUNCTION certificados_alta() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    m record;
    anterior record;
    sujeto bigint := app_sujeto();
BEGIN
    IF NEW.estado <> 'EMITIDO' OR NEW.revocado_en IS NOT NULL OR NEW.revocado_por_medicion_id IS NOT NULL THEN
        RAISE EXCEPTION 'O6: un certificado nace EMITIDO.' USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT md.estado, md.parcela_id, md.periodo_id, md.parcela_version_id, md.toneladas_co2,
           md.verificador_id, f.propietario_id
      INTO m
      FROM mediciones_medicion md
      JOIN catalogo_parcela p ON p.id = md.parcela_id
      JOIN catalogo_finca f ON f.id = p.finca_id
     WHERE md.id = NEW.medicion_id;
    IF NOT FOUND OR m.estado <> 'APROBADA' THEN
        RAISE EXCEPTION 'O6: solo se emite el certificado de una medición APROBADA.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF sujeto IS NOT NULL AND m.verificador_id IS DISTINCT FROM sujeto THEN
        RAISE EXCEPTION 'C2: solo la aprobación del verificador asignado emite el certificado.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.parcela_id <> m.parcela_id OR NEW.periodo_id <> m.periodo_id
       OR NEW.parcela_version_id <> m.parcela_version_id OR NEW.toneladas_co2 <> m.toneladas_co2
       OR NEW.propietario_id <> m.propietario_id THEN
        RAISE EXCEPTION 'O6: el certificado no coincide con la medición aprobada.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (NEW.contenido::jsonb #>> '{{certificado,id}}') IS DISTINCT FROM NEW.id::text
       OR (NEW.contenido::jsonb ->> 'toneladas_co2')::numeric IS DISTINCT FROM NEW.toneladas_co2 THEN
        RAISE EXCEPTION 'O6: el documento firmado no corresponde a la fila.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.reemplaza_a_id IS NOT NULL THEN
        SELECT estado, parcela_id, periodo_id, revocado_por_medicion_id INTO anterior
          FROM certificados_certificado WHERE id = NEW.reemplaza_a_id;
        IF NOT FOUND OR anterior.estado <> 'REVOCADO' OR anterior.parcela_id <> NEW.parcela_id
           OR anterior.periodo_id <> NEW.periodo_id
           OR anterior.revocado_por_medicion_id IS DISTINCT FROM NEW.medicion_id THEN
            RAISE EXCEPTION 'P2: solo se reemplaza el certificado revocado por esta misma versión.'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER certificado_alta BEFORE INSERT ON certificados_certificado
    FOR EACH ROW EXECUTE FUNCTION certificados_alta();

CREATE OR REPLACE FUNCTION certificados_cambio() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v record;
    sujeto bigint := app_sujeto();
BEGIN
    IF OLD.estado <> 'EMITIDO' OR NEW.estado <> 'REVOCADO' THEN
        RAISE EXCEPTION 'P2: un certificado solo pasa de EMITIDO a REVOCADO, una vez.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.id <> OLD.id OR NEW.parcela_id <> OLD.parcela_id OR NEW.periodo_id <> OLD.periodo_id
       OR NEW.parcela_version_id <> OLD.parcela_version_id OR NEW.medicion_id <> OLD.medicion_id
       OR NEW.propietario_id <> OLD.propietario_id OR NEW.toneladas_co2 <> OLD.toneladas_co2
       OR NEW.emitido_en <> OLD.emitido_en OR NEW.reemplaza_a_id IS DISTINCT FROM OLD.reemplaza_a_id
       OR NEW.contenido <> OLD.contenido OR NEW.firma <> OLD.firma OR NEW.clave_publica <> OLD.clave_publica
       OR NEW.huella_clave <> OLD.huella_clave THEN
        RAISE EXCEPTION 'O6: un certificado emitido no se modifica; solo se revoca.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT estado, parcela_id, periodo_id, version_de_id, toneladas_co2, verificador_id INTO v
      FROM mediciones_medicion WHERE id = NEW.revocado_por_medicion_id;
    IF NOT FOUND OR v.estado <> 'APROBADA' OR v.version_de_id IS NULL
       OR v.parcela_id <> OLD.parcela_id OR v.periodo_id <> OLD.periodo_id THEN
        RAISE EXCEPTION 'P2: solo revoca una versión enmendada APROBADA de la misma parcela y período.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF v.toneladas_co2 = OLD.toneladas_co2 THEN
        RAISE EXCEPTION 'P2: si las toneladas no cambian, el certificado sigue vigente.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF sujeto IS NOT NULL AND v.verificador_id IS DISTINCT FROM sujeto THEN
        RAISE EXCEPTION 'C2: solo la aprobación del verificador asignado revoca el certificado.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER certificado_cambio BEFORE UPDATE ON certificados_certificado
    FOR EACH ROW EXECUTE FUNCTION certificados_cambio();

CREATE OR REPLACE FUNCTION certificados_sin_borrado() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'RG1: los certificados nunca se borran (P2 conserva el histórico).'
        USING ERRCODE = 'insufficient_privilege';
END
$$;
CREATE TRIGGER certificado_sin_borrado BEFORE DELETE ON certificados_certificado
    FOR EACH ROW EXECUTE FUNCTION certificados_sin_borrado();

ALTER TABLE certificados_certificado ENABLE ROW LEVEL SECURITY;
ALTER TABLE certificados_certificado FORCE ROW LEVEL SECURITY;
CREATE POLICY certificado_sistema ON certificados_certificado FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');
CREATE POLICY certificado_interna ON certificados_certificado FOR SELECT
    USING (app_fase() = 'verificacion' AND current_user <> session_user);
CREATE POLICY certificado_lectura ON certificados_certificado FOR SELECT
    USING ({NO_PREINGRESO} AND app_sujeto() IS NOT NULL AND (
               app_tiene_rol('GC') OR app_tiene_rol('AE')
            OR (propietario_id = app_sujeto() AND estado IN ('EMITIDO', 'REVOCADO'))
            OR {VF_DE_LA_PARCELA}));
CREATE POLICY certificado_alta ON certificados_certificado FOR INSERT
    WITH CHECK (app_fase() = 'emision' AND app_tiene_rol('VF') AND EXISTS (
        SELECT 1 FROM mediciones_medicion m
         WHERE m.id = medicion_id AND m.estado = 'APROBADA' AND m.verificador_id = app_sujeto()));
CREATE POLICY certificado_revocacion ON certificados_certificado FOR UPDATE
    USING ({VF_DE_LA_PARCELA})
    WITH CHECK (estado = 'REVOCADO' AND app_fase() = 'emision' AND EXISTS (
        SELECT 1 FROM mediciones_medicion v
         WHERE v.id = revocado_por_medicion_id AND v.estado = 'APROBADA' AND v.verificador_id = app_sujeto()));

CREATE OR REPLACE FUNCTION certificado_estado_publico(p_id uuid, p_firma text)
    RETURNS TABLE (estado text, revocado_en timestamptz, reemplazado_por uuid)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    SET app.fase = 'verificacion'
    AS $$
        SELECT c.estado::text, c.revocado_en,
               (SELECT n.id FROM public.certificados_certificado n WHERE n.reemplaza_a_id = c.id)
          FROM public.certificados_certificado c
         WHERE c.id = p_id AND c.firma = p_firma
    $$;
REVOKE ALL ON FUNCTION certificado_estado_publico(uuid, text) FROM PUBLIC;

DO $$
BEGIN
    IF to_regrole('app_runtime') IS NOT NULL THEN
        REVOKE DELETE, TRUNCATE ON certificados_certificado FROM app_runtime;
        GRANT SELECT, INSERT, UPDATE ON certificados_certificado TO app_runtime;
        GRANT EXECUTE ON FUNCTION certificado_estado_publico(uuid, text) TO app_runtime;
    END IF;
END
$$;
"""

REVERSO = """
DROP FUNCTION IF EXISTS certificado_estado_publico(uuid, text);
DROP POLICY IF EXISTS certificado_revocacion ON certificados_certificado;
DROP POLICY IF EXISTS certificado_alta ON certificados_certificado;
DROP POLICY IF EXISTS certificado_lectura ON certificados_certificado;
DROP POLICY IF EXISTS certificado_interna ON certificados_certificado;
DROP POLICY IF EXISTS certificado_sistema ON certificados_certificado;
ALTER TABLE certificados_certificado NO FORCE ROW LEVEL SECURITY;
ALTER TABLE certificados_certificado DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS certificado_sin_borrado ON certificados_certificado;
DROP FUNCTION IF EXISTS certificados_sin_borrado();
DROP TRIGGER IF EXISTS certificado_cambio ON certificados_certificado;
DROP FUNCTION IF EXISTS certificados_cambio();
DROP TRIGGER IF EXISTS certificado_alta ON certificados_certificado;
DROP FUNCTION IF EXISTS certificados_alta();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("certificados", "0001_initial"),
        ("mediciones", "0003_disparadores"),
        ("cuentas", "0024_rls_tablas_django"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)]
