GRANT CONNECT ON DATABASE neondb TO app_runtime;

-- Sin USAGE sobre el esquema, el rol no puede ni resolver los nombres de las
-- tablas, aunque tenga privilegios sobre ellas.
GRANT USAGE ON SCHEMA public TO app_runtime;


GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_runtime;

-- Las secuencias hacen falta para que los INSERT con columnas serial/identity
-- puedan obtener el siguiente valor (nextval).
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime;


ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_runtime;

-- Aun así, si por algún motivo se migra con otro rol (o los privilegios por
-- defecto no se aplicaron), ejecuta `db/grant_after_migrate.sql` después de
-- cada migrate: hace el GRANT de forma dinámica sobre todo lo existente.


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
