-- ===========================================================================
-- db/roles_rls.sql
-- ---------------------------------------------------------------------------
-- Preparación de la base PostgreSQL (Neon) para que la aplicación corra con un
-- rol NO dueño de las tablas y Row Level Security (RLS) le aplique de verdad.
--
-- ¿Por qué? En PostgreSQL el DUEÑO de una tabla se salta sus políticas RLS.
-- Si Django se conectara con el mismo rol que ejecuta `migrate`, todas las
-- políticas de Persona 3 serían decorativas. Por eso separamos dos roles:
--
--   * Rol DUEÑO  (en Neon suele ser `neondb_owner`): corre las MIGRACIONES (DDL).
--   * Rol de APP (`app_runtime`): corre la aplicación en runtime; solo DML y
--     sujeto a RLS.
--
-- CUÁNDO SE EJECUTA ESTE ARCHIVO:
--   UNA sola vez, como rol DUEÑO, DESPUÉS de `python manage.py migrate`
--   (las tablas deben existir para poder otorgar privilegios sobre ellas).
--
-- CÓMO EJECUTARLO:
--   psql "$DATABASE_URL_DUENO" -f db/roles_rls.sql
--   (o pegando el contenido en el SQL Editor de la consola de Neon)
--
-- El script es idempotente en su mayoría: GRANT, ALTER TABLE ... ENABLE/FORCE
-- y ALTER DEFAULT PRIVILEGES se pueden repetir sin error.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- 0) Creación del rol de aplicación
-- ---------------------------------------------------------------------------
-- El rol se crea UNA vez por SQL con el rol DUEÑO, cambiando <password> por
-- una contraseña aleatoria (esa contraseña va solo a Secret Manager, nunca al
-- repo):
--
--   CREATE ROLE app_runtime LOGIN PASSWORD '<password>'
--       NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
--
-- NO usar la consola ni la API de Neon para crear este rol: en este proyecto
-- esas vías lo crean con BYPASSRLS, CREATEDB, CREATEROLE y membresía en
-- neon_superuser, y eso anula RLS. Si el rol ya existe con esos atributos, no
-- se pueden quitar por SQL (hace falta superusuario): hay que borrarlo y
-- recrearlo.
--
-- Verificación (debe dar f|f|f|f y 0 membresías):
--   SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole
--     FROM pg_roles WHERE rolname = 'app_runtime';
--   SELECT count(*) FROM pg_auth_members m
--     JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = 'app_runtime';


-- ---------------------------------------------------------------------------
-- 1) Acceso a la base de datos y al esquema
-- ---------------------------------------------------------------------------
-- El nombre real de la base en Neon es normalmente `neondb`. Si usas otro,
-- cámbialo en la siguiente línea (no se puede parametrizar de forma portable
-- entre psql y el editor SQL de Neon).
GRANT CONNECT ON DATABASE neondb TO app_runtime;

-- Sin USAGE sobre el esquema, el rol no puede ni resolver los nombres de las
-- tablas, aunque tenga privilegios sobre ellas.
GRANT USAGE ON SCHEMA public TO app_runtime;


-- ---------------------------------------------------------------------------
-- 2) Privilegios DML sobre lo que YA existe
-- ---------------------------------------------------------------------------
-- Solo DML: la aplicación nunca debe hacer DDL (crear/alterar/borrar tablas).
-- Esto incluye también las tablas de Django/contrib (auth_*, django_session,
-- django_admin_log, django_migrations...), necesarias para que la app funcione:
-- sesiones, login y admin. Esas tablas NO llevan RLS (ver sección 4).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_runtime;

-- Las secuencias hacen falta para que los INSERT con columnas serial/identity
-- puedan obtener el siguiente valor (nextval).
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime;


-- ---------------------------------------------------------------------------
-- 3) Privilegios sobre objetos FUTUROS
-- ---------------------------------------------------------------------------
-- Los GRANT de la sección 2 solo cubren las tablas que existen HOY. Cada
-- `manage.py migrate` puede crear tablas y secuencias nuevas, y esas no
-- heredarían los permisos. ALTER DEFAULT PRIVILEGES deja la regla escrita para
-- que todo objeto futuro del esquema public, creado por el rol DUEÑO que
-- ejecuta esta sentencia, nazca con los permisos ya dados.
--
-- IMPORTANTE: ALTER DEFAULT PRIVILEGES solo afecta a objetos creados por el rol
-- que lo ejecuta (o por los roles indicados en FOR ROLE). Por eso este archivo
-- debe correrse con el MISMO rol dueño que después ejecuta `migrate`.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_runtime;

-- Aun así, si por algún motivo se migra con otro rol (o los privilegios por
-- defecto no se aplicaron), ejecuta `db/grant_after_migrate.sql` después de
-- cada migrate: hace el GRANT de forma dinámica sobre todo lo existente.


-- ---------------------------------------------------------------------------
-- 4) Row Level Security SOLO en las tablas de la aplicación
-- ---------------------------------------------------------------------------
-- Se habilitan únicamente las tablas propias del dominio. Las tablas de
-- Django/contrib se dejan fuera a propósito: Django las gestiona (sesiones,
-- migraciones, permisos) y forzar RLS ahí rompería el arranque, el admin y el
-- login.
--
-- ENABLE ROW LEVEL SECURITY: activa las políticas para los roles normales.
-- FORCE  ROW LEVEL SECURITY: hace que las políticas apliquen TAMBIÉN al dueño
--   de la tabla. Sin FORCE, el rol dueño (que es el que corre las migraciones)
--   seguiría viendo y escribiendo todo, y las pruebas de aislamiento darían
--   falsos positivos.
--
-- NOTA: aquí solo se habilita el mecanismo. Las POLÍTICAS concretas
-- (CREATE POLICY ... USING / WITH CHECK) las define Persona 3 según la matriz
-- de permisos. Hasta que existan políticas, con RLS activo y forzado nadie ve
-- filas: es el estado seguro por defecto (deny-all).

-- 4.1 · Cuentas
ALTER TABLE cuentas_usuario ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuentas_usuario FORCE ROW LEVEL SECURITY;

-- 4.2 · Catálogo (O1a y O1b)
ALTER TABLE catalogo_finca ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalogo_finca FORCE ROW LEVEL SECURITY;

ALTER TABLE catalogo_datospropietario ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalogo_datospropietario FORCE ROW LEVEL SECURITY;

ALTER TABLE catalogo_parcela ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalogo_parcela FORCE ROW LEVEL SECURITY;

ALTER TABLE catalogo_parcelaversion ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalogo_parcelaversion FORCE ROW LEVEL SECURITY;

-- 4.3 · Mediciones, períodos y enmiendas (O2–O5)
ALTER TABLE mediciones_periodo ENABLE ROW LEVEL SECURITY;
ALTER TABLE mediciones_periodo FORCE ROW LEVEL SECURITY;

ALTER TABLE mediciones_asignacion ENABLE ROW LEVEL SECURITY;
ALTER TABLE mediciones_asignacion FORCE ROW LEVEL SECURITY;

ALTER TABLE mediciones_motivodevolucion ENABLE ROW LEVEL SECURITY;
ALTER TABLE mediciones_motivodevolucion FORCE ROW LEVEL SECURITY;

ALTER TABLE mediciones_medicion ENABLE ROW LEVEL SECURITY;
ALTER TABLE mediciones_medicion FORCE ROW LEVEL SECURITY;

ALTER TABLE mediciones_medicionactor ENABLE ROW LEVEL SECURITY;
ALTER TABLE mediciones_medicionactor FORCE ROW LEVEL SECURITY;

ALTER TABLE mediciones_enmienda ENABLE ROW LEVEL SECURITY;
ALTER TABLE mediciones_enmienda FORCE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- 5) Verificación rápida (opcional, solo lectura)
-- ---------------------------------------------------------------------------
-- Debe mostrar relrowsecurity = true y relforcerowsecurity = true para las 11
-- tablas de la aplicación, y false para las de Django/contrib.
--
-- SELECT c.relname,
--        c.relrowsecurity       AS rls_activo,
--        c.relforcerowsecurity  AS rls_forzado
--   FROM pg_class c
--   JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'public'
--    AND c.relkind = 'r'
--  ORDER BY c.relname;
