"""
RLS de C7 (AC·O7b AR, RR; GC·O7b AU, RE).

  - cuentas_solicitudrol: la leen AC, GC y AE (O7b L). AC crea solicitudes a
    su nombre para otras cuentas. Un GC distinto del solicitante y de la
    persona destino la resuelve a su nombre. Una solicitud resuelta no cambia,
    y nadie cambia a quién, qué rol o quién la pidió (disparador).
  - cuentas_rolusuario: AC da y retira solo roles sin doble aprobación (nunca
    GC ni AE, nunca a sí mismo). GC da o retira GC/AE solo si existe una
    solicitud de ese cambio AUTORIZADA por él.
  - cuentas_usuario: GC puede actualizar (activar) una cuenta ajena solo si
    tiene una solicitud en curso o resuelta por él.
  - cuentas_sesion: AC y GC cierran las sesiones de la cuenta cuyo rol cambia.
"""

from django.db import migrations

SQL = """
ALTER TABLE cuentas_solicitudrol ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuentas_solicitudrol FORCE ROW LEVEL SECURITY;
CREATE POLICY solicitudrol_lectura ON cuentas_solicitudrol FOR SELECT
    USING (app_tiene_rol('AC') OR app_tiene_rol('GC') OR app_tiene_rol('AE'));
CREATE POLICY solicitudrol_alta ON cuentas_solicitudrol FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND solicitada_por_id = app_sujeto()
                AND usuario_id <> app_sujeto() AND estado = 'SOLICITADA');
CREATE POLICY solicitudrol_resolucion ON cuentas_solicitudrol FOR UPDATE
    USING (app_tiene_rol('GC') AND estado = 'SOLICITADA'
           AND solicitada_por_id <> app_sujeto() AND usuario_id <> app_sujeto())
    WITH CHECK (resuelta_por_id = app_sujeto() AND estado IN ('AUTORIZADA', 'RECHAZADA'));
CREATE POLICY solicitudrol_sistema ON cuentas_solicitudrol FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

CREATE OR REPLACE FUNCTION cuentas_solicitudrol_inmutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF app_fase() IS NOT DISTINCT FROM 'sistema' THEN
        RETURN NEW;
    END IF;
    IF OLD.estado <> 'SOLICITADA'
        OR NEW.usuario_id IS DISTINCT FROM OLD.usuario_id
        OR NEW.rol IS DISTINCT FROM OLD.rol
        OR NEW.tipo IS DISTINCT FROM OLD.tipo
        OR NEW.solicitada_por_id IS DISTINCT FROM OLD.solicitada_por_id
        OR NEW.solicitada_en IS DISTINCT FROM OLD.solicitada_en
    THEN
        RAISE EXCEPTION 'Una solicitud de rol resuelta no cambia, ni su destino, rol o solicitante.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER solicitudrol_inmutable BEFORE UPDATE ON cuentas_solicitudrol
    FOR EACH ROW EXECUTE FUNCTION cuentas_solicitudrol_inmutable();

DROP POLICY rolusuario_gestion_alta ON cuentas_rolusuario;
CREATE POLICY rolusuario_gestion_alta ON cuentas_rolusuario FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND usuario_id <> app_sujeto() AND rol NOT IN ('GC', 'AE'));
CREATE POLICY rolusuario_gestion_baja ON cuentas_rolusuario FOR UPDATE
    USING (app_tiene_rol('AC') AND usuario_id <> app_sujeto() AND rol NOT IN ('GC', 'AE'))
    WITH CHECK (usuario_id <> app_sujeto() AND rol NOT IN ('GC', 'AE'));
CREATE POLICY rolusuario_autorizada_alta ON cuentas_rolusuario FOR INSERT
    WITH CHECK (
        app_tiene_rol('GC') AND usuario_id <> app_sujeto() AND EXISTS (
            SELECT 1 FROM cuentas_solicitudrol s
             WHERE s.usuario_id = cuentas_rolusuario.usuario_id AND s.rol = cuentas_rolusuario.rol
               AND s.tipo = 'ASIGNAR' AND s.estado = 'AUTORIZADA' AND s.resuelta_por_id = app_sujeto()
        )
    );
CREATE POLICY rolusuario_autorizada_baja ON cuentas_rolusuario FOR UPDATE
    USING (
        app_tiene_rol('GC') AND usuario_id <> app_sujeto() AND EXISTS (
            SELECT 1 FROM cuentas_solicitudrol s
             WHERE s.usuario_id = cuentas_rolusuario.usuario_id AND s.rol = cuentas_rolusuario.rol
               AND s.tipo = 'RETIRAR' AND s.estado = 'AUTORIZADA' AND s.resuelta_por_id = app_sujeto()
        )
    )
    WITH CHECK (usuario_id <> app_sujeto());

CREATE POLICY usuario_gestion_gc ON cuentas_usuario FOR UPDATE
    USING (
        app_tiene_rol('GC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff AND EXISTS (
            SELECT 1 FROM cuentas_solicitudrol s
             WHERE s.usuario_id = cuentas_usuario.id
               AND (s.estado = 'SOLICITADA' OR s.resuelta_por_id = app_sujeto())
        )
    )
    WITH CHECK (id <> app_sujeto() AND NOT is_superuser AND NOT is_staff);

DROP POLICY sesion_gestion_lectura ON cuentas_sesion;
DROP POLICY sesion_gestion_cierre ON cuentas_sesion;
CREATE POLICY sesion_gestion_lectura ON cuentas_sesion FOR SELECT
    USING (app_tiene_rol('AC') OR app_tiene_rol('GC'));
CREATE POLICY sesion_gestion_cierre ON cuentas_sesion FOR UPDATE
    USING ((app_tiene_rol('AC') OR app_tiene_rol('GC')) AND usuario_id <> app_sujeto())
    WITH CHECK (usuario_id <> app_sujeto() AND cerrada_en IS NOT NULL);
"""

REVERSO = """
DROP POLICY IF EXISTS sesion_gestion_lectura ON cuentas_sesion;
DROP POLICY IF EXISTS sesion_gestion_cierre ON cuentas_sesion;
CREATE POLICY sesion_gestion_lectura ON cuentas_sesion FOR SELECT
    USING (app_tiene_rol('AC'));
CREATE POLICY sesion_gestion_cierre ON cuentas_sesion FOR UPDATE
    USING (app_tiene_rol('AC') AND usuario_id <> app_sujeto())
    WITH CHECK (usuario_id <> app_sujeto() AND cerrada_en IS NOT NULL);

DROP POLICY IF EXISTS usuario_gestion_gc ON cuentas_usuario;

DROP POLICY IF EXISTS rolusuario_autorizada_baja ON cuentas_rolusuario;
DROP POLICY IF EXISTS rolusuario_autorizada_alta ON cuentas_rolusuario;
DROP POLICY IF EXISTS rolusuario_gestion_baja ON cuentas_rolusuario;
DROP POLICY IF EXISTS rolusuario_gestion_alta ON cuentas_rolusuario;
CREATE POLICY rolusuario_gestion_alta ON cuentas_rolusuario FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND usuario_id <> app_sujeto());

DROP TRIGGER IF EXISTS solicitudrol_inmutable ON cuentas_solicitudrol;
DROP FUNCTION IF EXISTS cuentas_solicitudrol_inmutable();
DROP POLICY IF EXISTS solicitudrol_lectura ON cuentas_solicitudrol;
DROP POLICY IF EXISTS solicitudrol_alta ON cuentas_solicitudrol;
DROP POLICY IF EXISTS solicitudrol_resolucion ON cuentas_solicitudrol;
DROP POLICY IF EXISTS solicitudrol_sistema ON cuentas_solicitudrol;
ALTER TABLE cuentas_solicitudrol NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cuentas_solicitudrol DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0008_solicitudes_rol")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
