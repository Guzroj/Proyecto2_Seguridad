"""
Row Level Security de las tablas de cuentas.

Se aplica con el rol DUEÑO en la migración (job verdeazul-migrate) y queda
versionado. Complementa db/roles_rls.sql, que ya habilita RLS en cuentas_usuario.

Políticas (variables de conexión fijadas por cuentas.rls):
  - Anónimo (sin app.usuario_id): no ve ninguna cuenta. La única vía para el
    login es cuentas_id_para_login(username), SECURITY DEFINER, que devuelve
    solo el id de una cuenta ACTIVA.
  - Autenticado: lee cuentas y roles (los servicios eligen verificador, validan
    roles, muestran autores). Solo modifica su propia fila (last_login, contraseña).
  - app.fase = 'sistema': procesos sin usuario (tareas, carga inicial).
Las políticas de gestión por AC/GC (C15, C7) se agregan con esos servicios.

Además, un disparador impide que un usuario cambie en su propia fila el estado,
los indicadores de superusuario/staff o su username (escalada de privilegios).
"""

from django.db import migrations

TABLAS_LECTURA_AUTENTICADA = [
    "cuentas_rolusuario",
    "cuentas_usuario_groups",
    "cuentas_usuario_user_permissions",
]

SQL = """
CREATE OR REPLACE FUNCTION app_sujeto() RETURNS bigint
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.usuario_id', true), '')::bigint $$;

CREATE OR REPLACE FUNCTION app_fase() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.fase', true), '') $$;

CREATE OR REPLACE FUNCTION cuentas_id_para_login(p_username text) RETURNS bigint
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    SET app.fase = 'login'
    AS $$
        SELECT id FROM public.cuentas_usuario
         WHERE username = p_username AND estado = 'ACTIVA'
    $$;
REVOKE ALL ON FUNCTION cuentas_id_para_login(text) FROM PUBLIC;
DO $$
BEGIN
    IF to_regrole('app_runtime') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION cuentas_id_para_login(text) TO app_runtime;
    END IF;
END
$$;

ALTER TABLE cuentas_usuario ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuentas_usuario FORCE ROW LEVEL SECURITY;

-- Solo dentro de cuentas_id_para_login (corre como su dueño: current_user <> session_user).
CREATE POLICY usuario_login ON cuentas_usuario FOR SELECT
    USING (app_fase() = 'login' AND current_user <> session_user);
CREATE POLICY usuario_lectura ON cuentas_usuario FOR SELECT
    USING (app_sujeto() IS NOT NULL OR app_fase() = 'sistema');
CREATE POLICY usuario_propia ON cuentas_usuario FOR UPDATE
    USING (id = app_sujeto()) WITH CHECK (id = app_sujeto());
CREATE POLICY usuario_sistema ON cuentas_usuario FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

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
CREATE TRIGGER usuario_sin_autoescalada BEFORE UPDATE ON cuentas_usuario
    FOR EACH ROW EXECUTE FUNCTION cuentas_usuario_sin_autoescalada();
"""

for tabla in TABLAS_LECTURA_AUTENTICADA:
    SQL += f"""
ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {tabla} FORCE ROW LEVEL SECURITY;
CREATE POLICY {tabla}_lectura ON {tabla} FOR SELECT
    USING (app_sujeto() IS NOT NULL OR app_fase() = 'sistema');
CREATE POLICY {tabla}_sistema ON {tabla} FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');
"""

REVERSO = """
DROP TRIGGER IF EXISTS usuario_sin_autoescalada ON cuentas_usuario;
DROP FUNCTION IF EXISTS cuentas_usuario_sin_autoescalada();
DROP POLICY IF EXISTS usuario_login ON cuentas_usuario;
DROP POLICY IF EXISTS usuario_lectura ON cuentas_usuario;
DROP POLICY IF EXISTS usuario_propia ON cuentas_usuario;
DROP POLICY IF EXISTS usuario_sistema ON cuentas_usuario;
DROP FUNCTION IF EXISTS cuentas_id_para_login(text);
"""
for tabla in TABLAS_LECTURA_AUTENTICADA:
    REVERSO += f"""
DROP POLICY IF EXISTS {tabla}_lectura ON {tabla};
DROP POLICY IF EXISTS {tabla}_sistema ON {tabla};
ALTER TABLE {tabla} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {tabla} DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0003_roles_con_vigencia")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
