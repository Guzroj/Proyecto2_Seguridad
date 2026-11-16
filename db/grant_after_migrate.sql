-- ===========================================================================
-- db/grant_after_migrate.sql
-- ---------------------------------------------------------------------------
-- Otorga privilegios DML al rol de aplicación (`app_runtime`) sobre TODAS las
-- tablas y secuencias del esquema `public`, y EXECUTE sobre las funciones
-- SECURITY DEFINER que la app necesita (login, media de lecturas y datos del
-- certificado).
--
-- ¿CUÁNDO SE EJECUTA?
--   Como rol DUEÑO, DESPUÉS de CADA `python manage.py migrate`.
--   Motivo: una migración puede crear tablas y secuencias nuevas, y los GRANT
--   de `db/roles_rls.sql` no se aplican retroactivamente.
--
--   Comando habitual tras migrar:
--     python manage.py migrate
--     psql "$DATABASE_URL_DUENO" -f db/grant_after_migrate.sql
--
-- Es IDEMPOTENTE: repetirlo no da error ni concede privilegios de más.
--
-- NOTA: si el rol dueño que ejecuta `migrate` es el mismo que ejecuta este
-- script, `ALTER DEFAULT PRIVILEGES` (ver `db/roles_rls.sql`) ya cubriría las
-- tablas futuras y este archivo solo sería una red de seguridad. Se mantiene
-- porque es explícito, verificable y no depende de quién migró.
-- ===========================================================================

DO $$
DECLARE
    v_rol      text := 'app_runtime';          -- rol de la aplicación en runtime
    v_esquema  text := 'public';               -- esquema donde vive el dominio
    v_tabla    record;
    v_secuencia record;
BEGIN
    -- Si el rol aún no existe (no se creó con neonctl), avisamos y no hacemos
    -- nada: preferible un error explícito a un GRANT silencioso que no aplica.
    IF to_regrole(v_rol) IS NULL THEN
        RAISE EXCEPTION
            'El rol % no existe. Créalo antes con: neonctl roles create %',
            v_rol, v_rol;
    END IF;

    -- -----------------------------------------------------------------------
    -- 1) Tablas: SELECT, INSERT, UPDATE, DELETE
    -- -----------------------------------------------------------------------
    -- Recorremos pg_tables para incluir tanto las tablas de la aplicación como
    -- las de Django/contrib (auth_*, django_session, ...): la app las necesita
    -- para login, sesiones y admin. RLS no se toca aquí (eso es de roles_rls.sql).
    FOR v_tabla IN
        SELECT tablename
          FROM pg_tables
         WHERE schemaname = v_esquema
         ORDER BY tablename
    LOOP
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I.%I TO %I',
            v_esquema, v_tabla.tablename, v_rol
        );
    END LOOP;

    -- -----------------------------------------------------------------------
    -- 2) Secuencias: USAGE y SELECT (necesarios para nextval en los INSERT)
    -- -----------------------------------------------------------------------
    -- relkind = 'S' son secuencias "clásicas". Las columnas IDENTITY de
    -- PostgreSQL también se respaldan en una secuencia, así que quedan cubiertas.
    FOR v_secuencia IN
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = v_esquema
           AND c.relkind = 'S'
         ORDER BY c.relname
    LOOP
        EXECUTE format(
            'GRANT USAGE, SELECT ON SEQUENCE %I.%I TO %I',
            v_esquema, v_secuencia.relname, v_rol
        );
    END LOOP;

    -- -----------------------------------------------------------------------
    -- 3) Dejamos constancia de los privilegios por defecto para lo futuro
    -- -----------------------------------------------------------------------
    -- Esto cubre las tablas/secuencias que cree el rol que ejecuta este script.
    -- Es idempotente y no afecta a los objetos ya existentes.
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
        v_esquema, v_rol
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I',
        v_esquema, v_rol
    );

    -- -----------------------------------------------------------------------
    -- 4) Funciones SECURITY DEFINER que la app necesita ejecutar
    -- -----------------------------------------------------------------------
    -- Las migraciones crean estas funciones con REVOKE ALL ... FROM PUBLIC y
    -- sólo dan EXECUTE a `app_runtime` SI EL ROL YA EXISTE. Si el rol se creó
    -- después de migrar, hay que otorgarlo aquí (idempotente). Se comprueba
    -- que la función exista para poder correr el script antes de esa migración.
    IF to_regprocedure(v_esquema || '.cuentas_id_para_login(text)') IS NOT NULL THEN
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %I.cuentas_id_para_login(text) TO %I',
            v_esquema, v_rol
        );
    END IF;
    IF to_regprocedure(v_esquema || '.cuentas_media_lecturas_rol(text, timestamptz, timestamptz)') IS NOT NULL THEN
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %I.cuentas_media_lecturas_rol(text, timestamptz, timestamptz) TO %I',
            v_esquema, v_rol
        );
    END IF;
    IF to_regprocedure(v_esquema || '.certificado_datos_propietario(uuid)') IS NOT NULL THEN
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %I.certificado_datos_propietario(uuid) TO %I',
            v_esquema, v_rol
        );
    END IF;

    RAISE NOTICE 'Privilegios DML y de funciones de % sobre el esquema % actualizados.', v_rol, v_esquema;
END
$$;
