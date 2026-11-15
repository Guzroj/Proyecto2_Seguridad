"""
RLS de C17 (restablecimiento del segundo factor con doble control).

  - cuentas_restablecimientofactor: la leen AC, GC y AE (O7d). AC solicita a su
    nombre para otra cuenta; un GC distinto del solicitante y de la persona la
    resuelve a su nombre. Resuelta, no cambia (disparador).
  - mfa_authenticator:
      * AC y AE leen las filas (metadatos, O7d L). El secreto va cifrado y la
        aplicación solo expone tipo y fechas (cuentas.selectores.metadatos_factores).
      * Un GC ve los metadatos de una cuenta con un restablecimiento en curso
        (decide con ellos) y elimina sus factores de una cuenta solo con un restablecimiento
        de esa cuenta AUTORIZADO por él en los últimos 5 minutos (la misma
        operación), así una autorización vieja no sirve para borrar factores
        registrados después.
"""

from django.db import migrations

SQL = """
ALTER TABLE cuentas_restablecimientofactor ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuentas_restablecimientofactor FORCE ROW LEVEL SECURITY;
CREATE POLICY restablecimiento_lectura ON cuentas_restablecimientofactor FOR SELECT
    USING (app_tiene_rol('AC') OR app_tiene_rol('GC') OR app_tiene_rol('AE'));
CREATE POLICY restablecimiento_alta ON cuentas_restablecimientofactor FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND solicitado_por_id = app_sujeto()
                AND usuario_id <> app_sujeto() AND estado = 'SOLICITADO');
CREATE POLICY restablecimiento_resolucion ON cuentas_restablecimientofactor FOR UPDATE
    USING (app_tiene_rol('GC') AND estado = 'SOLICITADO'
           AND solicitado_por_id <> app_sujeto() AND usuario_id <> app_sujeto())
    WITH CHECK (resuelto_por_id = app_sujeto() AND estado IN ('AUTORIZADO', 'RECHAZADO'));
CREATE POLICY restablecimiento_sistema ON cuentas_restablecimientofactor FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

CREATE OR REPLACE FUNCTION cuentas_restablecimiento_inmutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF app_fase() IS NOT DISTINCT FROM 'sistema' THEN
        RETURN NEW;
    END IF;
    IF OLD.estado <> 'SOLICITADO'
        OR NEW.usuario_id IS DISTINCT FROM OLD.usuario_id
        OR NEW.motivo IS DISTINCT FROM OLD.motivo
        OR NEW.solicitado_por_id IS DISTINCT FROM OLD.solicitado_por_id
        OR NEW.solicitado_en IS DISTINCT FROM OLD.solicitado_en
    THEN
        RAISE EXCEPTION 'Un restablecimiento resuelto no cambia, ni su cuenta, motivo o solicitante.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER restablecimiento_inmutable BEFORE UPDATE ON cuentas_restablecimientofactor
    FOR EACH ROW EXECUTE FUNCTION cuentas_restablecimiento_inmutable();

CREATE POLICY factor_metadatos ON mfa_authenticator FOR SELECT
    USING (app_tiene_rol('AC') OR app_tiene_rol('AE'));
CREATE POLICY factor_restablecimiento_lectura ON mfa_authenticator FOR SELECT
    USING (app_tiene_rol('GC') AND user_id <> app_sujeto() AND EXISTS (
        SELECT 1 FROM cuentas_restablecimientofactor r
         WHERE r.usuario_id = mfa_authenticator.user_id
           AND ((r.estado = 'SOLICITADO' AND r.solicitado_por_id <> app_sujeto())
                OR (r.estado = 'AUTORIZADO' AND r.resuelto_por_id = app_sujeto()
                    AND r.resuelto_en >= statement_timestamp() - interval '5 minutes'))
    ));
CREATE POLICY factor_restablecimiento ON mfa_authenticator FOR DELETE
    USING (app_tiene_rol('GC') AND user_id <> app_sujeto() AND EXISTS (
        SELECT 1 FROM cuentas_restablecimientofactor r
         WHERE r.usuario_id = mfa_authenticator.user_id AND r.estado = 'AUTORIZADO'
           AND r.resuelto_por_id = app_sujeto()
           AND r.resuelto_en >= statement_timestamp() - interval '5 minutes'
    ));
"""

REVERSO = """
DROP POLICY IF EXISTS factor_restablecimiento ON mfa_authenticator;
DROP POLICY IF EXISTS factor_restablecimiento_lectura ON mfa_authenticator;
DROP POLICY IF EXISTS factor_metadatos ON mfa_authenticator;
DROP TRIGGER IF EXISTS restablecimiento_inmutable ON cuentas_restablecimientofactor;
DROP FUNCTION IF EXISTS cuentas_restablecimiento_inmutable();
DROP POLICY IF EXISTS restablecimiento_lectura ON cuentas_restablecimientofactor;
DROP POLICY IF EXISTS restablecimiento_alta ON cuentas_restablecimientofactor;
DROP POLICY IF EXISTS restablecimiento_resolucion ON cuentas_restablecimientofactor;
DROP POLICY IF EXISTS restablecimiento_sistema ON cuentas_restablecimientofactor;
ALTER TABLE cuentas_restablecimientofactor NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cuentas_restablecimientofactor DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0014_restablecimiento_factor")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
