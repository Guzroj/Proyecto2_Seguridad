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

    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
        v_esquema, v_rol
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I',
        v_esquema, v_rol
    );

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

    IF to_regprocedure(v_esquema || '.certificado_estado_publico(uuid, text)') IS NOT NULL THEN
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %I.certificado_estado_publico(uuid, text) TO %I',
            v_esquema, v_rol
        );
    END IF;

    -- La bitácora es de solo inserción y los certificados nunca se borran.
    -- El GRANT general de arriba les dio UPDATE/DELETE: se quitan de nuevo (y TRUNCATE).
    IF to_regclass(v_esquema || '.bitacora_evento') IS NOT NULL THEN
        EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON TABLE %I.bitacora_evento FROM %I', v_esquema, v_rol);
    END IF;
    IF to_regclass(v_esquema || '.bitacora_sellobitacora') IS NOT NULL THEN
        EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON TABLE %I.bitacora_sellobitacora FROM %I', v_esquema, v_rol);
    END IF;
    IF to_regclass(v_esquema || '.certificados_certificado') IS NOT NULL THEN
        EXECUTE format('REVOKE DELETE, TRUNCATE ON TABLE %I.certificados_certificado FROM %I', v_esquema, v_rol);
    END IF;

    RAISE NOTICE 'Privilegios DML y de funciones de % sobre el esquema % actualizados.', v_rol, v_esquema;
END
$$;
