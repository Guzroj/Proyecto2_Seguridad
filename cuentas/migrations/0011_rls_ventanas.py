"""
RLS de las ventanas de auditoría (C5, C14).

  - cuentas_ventanaauditoria: la leen AC, GC y AE. AC la crea a su nombre para
    otra cuenta (siempre PENDIENTE). Un GC distinto del solicitante y del
    auditor la aprueba a su nombre. Aprobada, no cambia; nadie cambia su
    auditor, sus fechas ni quién la pidió (disparador).
  - cuentas_rolusuario: GC da el rol AE solo a quien tiene una ventana
    aprobada por él.
  - cuentas_usuario: GC activa la cuenta del auditor solo con una ventana
    pendiente o aprobada por él.
  - La tarea diaria corre en 'sistema'.
"""

from django.db import migrations

SQL = """
ALTER TABLE cuentas_ventanaauditoria ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuentas_ventanaauditoria FORCE ROW LEVEL SECURITY;
CREATE POLICY ventana_lectura ON cuentas_ventanaauditoria FOR SELECT
    USING (app_tiene_rol('AC') OR app_tiene_rol('GC') OR app_tiene_rol('AE'));
CREATE POLICY ventana_alta ON cuentas_ventanaauditoria FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND solicitada_por_id = app_sujeto()
                AND auditor_id <> app_sujeto() AND estado = 'PENDIENTE');
CREATE POLICY ventana_aprobacion ON cuentas_ventanaauditoria FOR UPDATE
    USING (app_tiene_rol('GC') AND estado = 'PENDIENTE'
           AND solicitada_por_id <> app_sujeto() AND auditor_id <> app_sujeto())
    WITH CHECK (estado = 'APROBADA' AND aprobada_por_id = app_sujeto());
CREATE POLICY ventana_sistema ON cuentas_ventanaauditoria FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

CREATE OR REPLACE FUNCTION cuentas_ventana_inmutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF app_fase() IS NOT DISTINCT FROM 'sistema' THEN
        RETURN NEW;
    END IF;
    IF OLD.estado <> 'PENDIENTE'
        OR NEW.auditor_id IS DISTINCT FROM OLD.auditor_id
        OR NEW.inicio IS DISTINCT FROM OLD.inicio
        OR NEW.fin IS DISTINCT FROM OLD.fin
        OR NEW.solicitada_por_id IS DISTINCT FROM OLD.solicitada_por_id
        OR NEW.solicitada_en IS DISTINCT FROM OLD.solicitada_en
    THEN
        RAISE EXCEPTION 'Una ventana aprobada no cambia, ni su auditor, sus fechas o su solicitante.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER ventana_inmutable BEFORE UPDATE ON cuentas_ventanaauditoria
    FOR EACH ROW EXECUTE FUNCTION cuentas_ventana_inmutable();

CREATE POLICY rolusuario_ventana_alta ON cuentas_rolusuario FOR INSERT
    WITH CHECK (
        app_tiene_rol('GC') AND rol = 'AE' AND usuario_id <> app_sujeto() AND EXISTS (
            SELECT 1 FROM cuentas_ventanaauditoria v
             WHERE v.auditor_id = cuentas_rolusuario.usuario_id
               AND v.estado = 'APROBADA' AND v.aprobada_por_id = app_sujeto()
        )
    );

CREATE POLICY usuario_ventana_gc ON cuentas_usuario FOR UPDATE
    USING (
        app_tiene_rol('GC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff AND EXISTS (
            SELECT 1 FROM cuentas_ventanaauditoria v
             WHERE v.auditor_id = cuentas_usuario.id
               AND (v.estado = 'PENDIENTE' OR v.aprobada_por_id = app_sujeto())
        )
    )
    WITH CHECK (id <> app_sujeto() AND NOT is_superuser AND NOT is_staff);
"""

REVERSO = """
DROP POLICY IF EXISTS usuario_ventana_gc ON cuentas_usuario;
DROP POLICY IF EXISTS rolusuario_ventana_alta ON cuentas_rolusuario;
DROP TRIGGER IF EXISTS ventana_inmutable ON cuentas_ventanaauditoria;
DROP FUNCTION IF EXISTS cuentas_ventana_inmutable();
DROP POLICY IF EXISTS ventana_lectura ON cuentas_ventanaauditoria;
DROP POLICY IF EXISTS ventana_alta ON cuentas_ventanaauditoria;
DROP POLICY IF EXISTS ventana_aprobacion ON cuentas_ventanaauditoria;
DROP POLICY IF EXISTS ventana_sistema ON cuentas_ventanaauditoria;
ALTER TABLE cuentas_ventanaauditoria NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cuentas_ventanaauditoria DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0010_ventanas_auditoria")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
