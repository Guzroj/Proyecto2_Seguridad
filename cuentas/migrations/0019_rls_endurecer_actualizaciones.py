"""
Endurece las actualizaciones de cuentas, roles y sesiones.

Con varias políticas permisivas de UPDATE en una tabla, PostgreSQL exige que la
fila vieja cumpla el USING de ALGUNA y la fila nueva el WITH CHECK de ALGUNA,
no necesariamente la misma. Así, la fila podía entrar por una política estrecha
(p. ej. el cierre de la baja de un GC) y salir por el WITH CHECK amplio de otra
(p. ej. reactivar la cuenta). Se cierra en dos capas:

  1. Cada WITH CHECK repite el rol y la condición de su propia política.
  2. Disparadores con lo que no puede cambiar nunca, sea cual sea la política
     (salvo la fase 'sistema'):
       - cuentas_usuario: el estado solo sigue las transiciones de la máquina de
         estados (PENDIENTE->ACTIVA|INACTIVA, ACTIVA->EN_BAJA|INACTIVA,
         EN_BAJA->INACTIVA). Nadie reabre una cuenta INACTIVA ni reactiva una EN_BAJA.
       - cuentas_rolusuario: quién, qué rol y desde cuándo no cambian; la
         vigencia solo se cierra o se acorta, nunca se alarga.
       - cuentas_sesion: quién y cuándo empezó no cambian; una sesión cerrada
         no se reabre ni cambia su motivo.
"""

from django.db import migrations

SQL = """
-- 1. WITH CHECK autosuficientes ----------------------------------------------
DROP POLICY usuario_gestion ON cuentas_usuario;
CREATE POLICY usuario_gestion ON cuentas_usuario FOR UPDATE
    USING (app_tiene_rol('AC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff)
    WITH CHECK (app_tiene_rol('AC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff);

DROP POLICY usuario_gestion_gc ON cuentas_usuario;
CREATE POLICY usuario_gestion_gc ON cuentas_usuario FOR UPDATE
    USING (
        app_tiene_rol('GC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff AND EXISTS (
            SELECT 1 FROM cuentas_solicitudrol s
             WHERE s.usuario_id = cuentas_usuario.id
               AND (s.estado = 'SOLICITADA' OR s.resuelta_por_id = app_sujeto())))
    WITH CHECK (
        app_tiene_rol('GC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff AND EXISTS (
            SELECT 1 FROM cuentas_solicitudrol s
             WHERE s.usuario_id = cuentas_usuario.id
               AND (s.estado = 'SOLICITADA' OR s.resuelta_por_id = app_sujeto())));

DROP POLICY usuario_ventana_gc ON cuentas_usuario;
CREATE POLICY usuario_ventana_gc ON cuentas_usuario FOR UPDATE
    USING (
        app_tiene_rol('GC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff AND EXISTS (
            SELECT 1 FROM cuentas_ventanaauditoria v
             WHERE v.auditor_id = cuentas_usuario.id
               AND (v.estado = 'PENDIENTE' OR v.aprobada_por_id = app_sujeto())))
    WITH CHECK (
        app_tiene_rol('GC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff AND EXISTS (
            SELECT 1 FROM cuentas_ventanaauditoria v
             WHERE v.auditor_id = cuentas_usuario.id
               AND (v.estado = 'PENDIENTE' OR v.aprobada_por_id = app_sujeto())));

DROP POLICY usuario_cierre_baja_gc ON cuentas_usuario;
CREATE POLICY usuario_cierre_baja_gc ON cuentas_usuario FOR UPDATE
    USING (app_tiene_rol('GC') AND id <> app_sujeto() AND estado = 'EN_BAJA'
           AND NOT is_superuser AND NOT is_staff)
    WITH CHECK (app_tiene_rol('GC') AND id <> app_sujeto() AND estado = 'INACTIVA' AND NOT is_active
                AND NOT is_superuser AND NOT is_staff);

DROP POLICY rolusuario_gestion_baja ON cuentas_rolusuario;
CREATE POLICY rolusuario_gestion_baja ON cuentas_rolusuario FOR UPDATE
    USING (app_tiene_rol('AC') AND usuario_id <> app_sujeto() AND rol NOT IN ('GC', 'AE'))
    WITH CHECK (app_tiene_rol('AC') AND usuario_id <> app_sujeto() AND rol NOT IN ('GC', 'AE'));

DROP POLICY rolusuario_autorizada_baja ON cuentas_rolusuario;
CREATE POLICY rolusuario_autorizada_baja ON cuentas_rolusuario FOR UPDATE
    USING (
        app_tiene_rol('GC') AND usuario_id <> app_sujeto() AND EXISTS (
            SELECT 1 FROM cuentas_solicitudrol s
             WHERE s.usuario_id = cuentas_rolusuario.usuario_id AND s.rol = cuentas_rolusuario.rol
               AND s.tipo = 'RETIRAR' AND s.estado = 'AUTORIZADA' AND s.resuelta_por_id = app_sujeto()))
    WITH CHECK (
        app_tiene_rol('GC') AND usuario_id <> app_sujeto() AND EXISTS (
            SELECT 1 FROM cuentas_solicitudrol s
             WHERE s.usuario_id = cuentas_rolusuario.usuario_id AND s.rol = cuentas_rolusuario.rol
               AND s.tipo = 'RETIRAR' AND s.estado = 'AUTORIZADA' AND s.resuelta_por_id = app_sujeto()));

DROP POLICY sesion_gestion_cierre ON cuentas_sesion;
CREATE POLICY sesion_gestion_cierre ON cuentas_sesion FOR UPDATE
    USING ((app_tiene_rol('AC') OR app_tiene_rol('GC')) AND usuario_id <> app_sujeto())
    WITH CHECK ((app_tiene_rol('AC') OR app_tiene_rol('GC')) AND usuario_id <> app_sujeto()
                AND cerrada_en IS NOT NULL);

-- 2. Lo que no cambia nunca ----------------------------------------------------
CREATE OR REPLACE FUNCTION cuentas_usuario_transicion_estado() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF app_fase() IS NOT DISTINCT FROM 'sistema' OR NEW.estado IS NOT DISTINCT FROM OLD.estado THEN
        RETURN NEW;
    END IF;
    IF (OLD.estado = 'PENDIENTE' AND NEW.estado IN ('ACTIVA', 'INACTIVA'))
        OR (OLD.estado = 'ACTIVA' AND NEW.estado IN ('EN_BAJA', 'INACTIVA'))
        OR (OLD.estado = 'EN_BAJA' AND NEW.estado = 'INACTIVA')
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Transición de cuenta no permitida: % -> %.', OLD.estado, NEW.estado
        USING ERRCODE = 'insufficient_privilege';
END
$$;
CREATE TRIGGER usuario_transicion_estado BEFORE UPDATE ON cuentas_usuario
    FOR EACH ROW EXECUTE FUNCTION cuentas_usuario_transicion_estado();

CREATE OR REPLACE FUNCTION cuentas_rolusuario_inmutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF app_fase() IS NOT DISTINCT FROM 'sistema' THEN
        RETURN NEW;
    END IF;
    IF NEW.usuario_id IS DISTINCT FROM OLD.usuario_id
        OR NEW.rol IS DISTINCT FROM OLD.rol
        OR NEW.vigente_desde IS DISTINCT FROM OLD.vigente_desde
        OR NEW.creado_en IS DISTINCT FROM OLD.creado_en
        OR NEW.vigente_hasta IS NULL AND OLD.vigente_hasta IS NOT NULL
        OR NEW.vigente_hasta > OLD.vigente_hasta
    THEN
        RAISE EXCEPTION 'Una pertenencia a un rol solo se cierra o se acorta.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER rolusuario_inmutable BEFORE UPDATE ON cuentas_rolusuario
    FOR EACH ROW EXECUTE FUNCTION cuentas_rolusuario_inmutable();

CREATE OR REPLACE FUNCTION cuentas_sesion_inmutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF app_fase() IS NOT DISTINCT FROM 'sistema' THEN
        RETURN NEW;
    END IF;
    IF NEW.usuario_id IS DISTINCT FROM OLD.usuario_id
        OR NEW.iniciada_en IS DISTINCT FROM OLD.iniciada_en
        OR (OLD.cerrada_en IS NOT NULL AND (NEW.cerrada_en IS DISTINCT FROM OLD.cerrada_en
                                            OR NEW.motivo_cierre IS DISTINCT FROM OLD.motivo_cierre))
    THEN
        RAISE EXCEPTION 'Una sesión no cambia de dueño ni de inicio, y cerrada no se reabre.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER sesion_inmutable BEFORE UPDATE ON cuentas_sesion
    FOR EACH ROW EXECUTE FUNCTION cuentas_sesion_inmutable();
"""

REVERSO = """
DROP TRIGGER IF EXISTS sesion_inmutable ON cuentas_sesion;
DROP FUNCTION IF EXISTS cuentas_sesion_inmutable();
DROP TRIGGER IF EXISTS rolusuario_inmutable ON cuentas_rolusuario;
DROP FUNCTION IF EXISTS cuentas_rolusuario_inmutable();
DROP TRIGGER IF EXISTS usuario_transicion_estado ON cuentas_usuario;
DROP FUNCTION IF EXISTS cuentas_usuario_transicion_estado();

DROP POLICY sesion_gestion_cierre ON cuentas_sesion;
CREATE POLICY sesion_gestion_cierre ON cuentas_sesion FOR UPDATE
    USING ((app_tiene_rol('AC') OR app_tiene_rol('GC')) AND usuario_id <> app_sujeto())
    WITH CHECK (usuario_id <> app_sujeto() AND cerrada_en IS NOT NULL);

DROP POLICY rolusuario_autorizada_baja ON cuentas_rolusuario;
CREATE POLICY rolusuario_autorizada_baja ON cuentas_rolusuario FOR UPDATE
    USING (
        app_tiene_rol('GC') AND usuario_id <> app_sujeto() AND EXISTS (
            SELECT 1 FROM cuentas_solicitudrol s
             WHERE s.usuario_id = cuentas_rolusuario.usuario_id AND s.rol = cuentas_rolusuario.rol
               AND s.tipo = 'RETIRAR' AND s.estado = 'AUTORIZADA' AND s.resuelta_por_id = app_sujeto()))
    WITH CHECK (usuario_id <> app_sujeto());

DROP POLICY rolusuario_gestion_baja ON cuentas_rolusuario;
CREATE POLICY rolusuario_gestion_baja ON cuentas_rolusuario FOR UPDATE
    USING (app_tiene_rol('AC') AND usuario_id <> app_sujeto() AND rol NOT IN ('GC', 'AE'))
    WITH CHECK (usuario_id <> app_sujeto() AND rol NOT IN ('GC', 'AE'));

DROP POLICY usuario_cierre_baja_gc ON cuentas_usuario;
CREATE POLICY usuario_cierre_baja_gc ON cuentas_usuario FOR UPDATE
    USING (app_tiene_rol('GC') AND id <> app_sujeto() AND estado = 'EN_BAJA'
           AND NOT is_superuser AND NOT is_staff)
    WITH CHECK (id <> app_sujeto() AND estado = 'INACTIVA' AND NOT is_active
                AND NOT is_superuser AND NOT is_staff);

DROP POLICY usuario_ventana_gc ON cuentas_usuario;
CREATE POLICY usuario_ventana_gc ON cuentas_usuario FOR UPDATE
    USING (
        app_tiene_rol('GC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff AND EXISTS (
            SELECT 1 FROM cuentas_ventanaauditoria v
             WHERE v.auditor_id = cuentas_usuario.id
               AND (v.estado = 'PENDIENTE' OR v.aprobada_por_id = app_sujeto())))
    WITH CHECK (id <> app_sujeto() AND NOT is_superuser AND NOT is_staff);

DROP POLICY usuario_gestion_gc ON cuentas_usuario;
CREATE POLICY usuario_gestion_gc ON cuentas_usuario FOR UPDATE
    USING (
        app_tiene_rol('GC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff AND EXISTS (
            SELECT 1 FROM cuentas_solicitudrol s
             WHERE s.usuario_id = cuentas_usuario.id
               AND (s.estado = 'SOLICITADA' OR s.resuelta_por_id = app_sujeto())))
    WITH CHECK (id <> app_sujeto() AND NOT is_superuser AND NOT is_staff);

DROP POLICY usuario_gestion ON cuentas_usuario;
CREATE POLICY usuario_gestion ON cuentas_usuario FOR UPDATE
    USING (app_tiene_rol('AC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff)
    WITH CHECK (id <> app_sujeto() AND NOT is_superuser AND NOT is_staff);
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0018_rls_cierre_baja")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
