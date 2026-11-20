"""
RLS de gestión de cuentas por AC (C15).

  - app_tiene_rol(r): el sujeto tiene hoy el rol r. Lee cuentas_rolusuario con
    los permisos del sujeto, así que en 'preingreso' (sin lectura de roles)
    siempre es falso.
  - cuentas_usuario: AC crea cuentas (siempre PENDIENTE, nunca superusuario ni
    staff) y modifica cuentas ajenas que no sean de superusuario ni staff. No
    puede tocar la propia por esta vía (u ≠ s).
  - cuentas_rolusuario: AC agrega roles a cuentas ajenas (rol inicial de la
    cuenta nueva; AR con doble aprobación en C7).
  - cuentas_sesion: AC ve y cierra las sesiones de cuentas ajenas (BA cierra
    sesiones).
  - El disparador ahora también impide cambiar la contraseña o el username de
    una cuenta AJENA (fuera de 'sistema'): AC nunca fija ni cambia contraseñas;
    la primera la pone la persona con el enlace de activación.
"""

from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION app_tiene_rol(p_rol text) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
        SELECT EXISTS (
            SELECT 1 FROM cuentas_rolusuario
             WHERE usuario_id = app_sujeto() AND rol = p_rol
               AND vigente_desde <= statement_timestamp()
               AND (vigente_hasta IS NULL OR vigente_hasta > statement_timestamp())
        )
    $$;

CREATE POLICY usuario_gestion_alta ON cuentas_usuario FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND estado = 'PENDIENTE' AND NOT is_active
                AND NOT is_superuser AND NOT is_staff);
CREATE POLICY usuario_gestion ON cuentas_usuario FOR UPDATE
    USING (app_tiene_rol('AC') AND id <> app_sujeto() AND NOT is_superuser AND NOT is_staff)
    WITH CHECK (id <> app_sujeto() AND NOT is_superuser AND NOT is_staff);

CREATE POLICY rolusuario_gestion_alta ON cuentas_rolusuario FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND usuario_id <> app_sujeto());

CREATE POLICY sesion_gestion_lectura ON cuentas_sesion FOR SELECT
    USING (app_tiene_rol('AC'));
CREATE POLICY sesion_gestion_cierre ON cuentas_sesion FOR UPDATE
    USING (app_tiene_rol('AC') AND usuario_id <> app_sujeto())
    WITH CHECK (usuario_id <> app_sujeto() AND cerrada_en IS NOT NULL);

CREATE OR REPLACE FUNCTION cuentas_usuario_sin_autoescalada() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF app_fase() IS NOT DISTINCT FROM 'sistema' THEN
        RETURN NEW;
    END IF;
    IF NEW.id = app_sujeto() AND (
           NEW.estado IS DISTINCT FROM OLD.estado
        OR NEW.is_active IS DISTINCT FROM OLD.is_active
        OR NEW.is_superuser IS DISTINCT FROM OLD.is_superuser
        OR NEW.is_staff IS DISTINCT FROM OLD.is_staff
        OR NEW.username IS DISTINCT FROM OLD.username
    ) THEN
        RAISE EXCEPTION 'Un usuario no cambia su propio estado, privilegios ni username.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF app_sujeto() IS NOT NULL AND NEW.id <> app_sujeto() AND (
           NEW.password IS DISTINCT FROM OLD.password
        OR NEW.username IS DISTINCT FROM OLD.username
    ) THEN
        RAISE EXCEPTION 'Nadie cambia la contraseña ni el username de otra cuenta.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
"""

REVERSO = """
DROP POLICY IF EXISTS usuario_gestion_alta ON cuentas_usuario;
DROP POLICY IF EXISTS usuario_gestion ON cuentas_usuario;
DROP POLICY IF EXISTS rolusuario_gestion_alta ON cuentas_rolusuario;
DROP POLICY IF EXISTS sesion_gestion_lectura ON cuentas_sesion;
DROP POLICY IF EXISTS sesion_gestion_cierre ON cuentas_sesion;
DROP FUNCTION IF EXISTS app_tiene_rol(text);

CREATE OR REPLACE FUNCTION cuentas_usuario_sin_autoescalada() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.id = app_sujeto() AND app_fase() IS DISTINCT FROM 'sistema' AND (
           NEW.estado IS DISTINCT FROM OLD.estado
        OR NEW.is_active IS DISTINCT FROM OLD.is_active
        OR NEW.is_superuser IS DISTINCT FROM OLD.is_superuser
        OR NEW.is_staff IS DISTINCT FROM OLD.is_staff
        OR NEW.username IS DISTINCT FROM OLD.username
    ) THEN
        RAISE EXCEPTION 'Un usuario no cambia su propio estado, privilegios ni username.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0006_rls_segundo_factor")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
