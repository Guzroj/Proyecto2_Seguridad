"""
Bitácora (O8): encadenamiento en la base, solo inserción y RLS con FORCE.

Encadenamiento (Arquitectura §4.5: h_n = SHA256(h_{n-1} || can(e_n))):
  bitacora_encadenar() es un disparador BEFORE INSERT que, con un candado consultivo de
  transacción, lee la cabeza de la cadena y fija secuencia, momento (clock_timestamp),
  hash_anterior y hash. Así:
    - la aplicación no elige su lugar en la cadena ni el hash;
    - dos transacciones simultáneas no se bifurcan: el candado las serializa y, aun si
      algo fallara, UNIQUE(hash_anterior) y UNIQUE(secuencia) impiden confirmar una
      bifurcación (fallo cerrado);
    - una transacción revertida no deja huecos: la siguiente recalcula desde la cabeza.
  Solo se acepta en READ COMMITTED: con una instantánea de transacción (REPEATABLE READ)
  la cabeza leída podría ser vieja, así que se rechaza.
  can(e) es el texto de un jsonb con todos los campos; jsonb ordena las claves de forma
  determinista. bitacora_verificar() recalcula la cadena y reporta el primer problema.

Solo inserción: un disparador rechaza UPDATE y DELETE fila por fila, también al dueño de
la tabla. TRUNCATE lo cubren los privilegios, como en el resto del dominio: app_runtime
pierde UPDATE, DELETE y TRUNCATE sobre la tabla (y db/grant_after_migrate.sql los vuelve
a quitar tras cada migrate); solo el dueño, que únicamente corre migraciones, lo tiene.

RLS (con FORCE):
  lectura   GC y AE (matriz: GC·O8 L, AE·O8 L/X [C5]); el motor decide C5 y la pantalla
            pasa por él. Nunca en preingreso.
  inserción quien actúa a su nombre (actor = sujeto), procesos del sistema (actor vacío,
            p. ej. el motor HBAC o la tarea diaria) o la fase 'sistema'.
  interna   el disparador (SECURITY DEFINER, fase 'bitacora') lee la cabeza: en Neon el
            dueño no es superusuario y RLS también le aplica.
Nadie actualiza ni borra.
"""

from django.db import migrations

NO_PREINGRESO = "app_fase() IS DISTINCT FROM 'preingreso'"
CANDADO = 800800008  # pg_advisory_xact_lock de la cadena (constante del proyecto)

SQL = rf"""
CREATE OR REPLACE FUNCTION bitacora_canonico(e bitacora_evento) RETURNS text
    LANGUAGE sql STABLE
    AS $$
        SELECT jsonb_build_object(
            'secuencia', e.secuencia,
            'momento', to_char(e.momento AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            'actor', e.actor_id,
            'actor_nombre', e.actor_nombre,
            'roles', e.roles,
            'accion', e.accion,
            'recurso_tipo', e.recurso_tipo,
            'recurso_id', e.recurso_id,
            'resultado', e.resultado,
            'antes', e.antes,
            'despues', e.despues,
            'detalle', e.detalle,
            'origen', e.origen
        )::text
    $$;

CREATE OR REPLACE FUNCTION bitacora_hash(p_anterior text, p_canonico text) RETURNS text
    LANGUAGE sql IMMUTABLE
    AS $$ SELECT encode(sha256(convert_to(p_anterior || p_canonico, 'UTF8')), 'hex') $$;

-- La fase se fija en el cuerpo y se restaura al salir. No se usa SET en la
-- definición de la función: PostgreSQL exige ser superusuario para fijar un
-- GUC propio (app.*) desde CREATE FUNCTION, y el rol dueño de Neon no lo es.
CREATE OR REPLACE FUNCTION bitacora_encadenar() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
DECLARE
    cabeza record;
    v_fase text := current_setting('app.fase', true);
BEGIN
    PERFORM set_config('app.fase', 'bitacora', true);
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Bitácora: solo se escribe en READ COMMITTED (la cabeza de la cadena debe ser la última confirmada).'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    PERFORM pg_advisory_xact_lock({CANDADO});
    SELECT secuencia, hash INTO cabeza FROM public.bitacora_evento ORDER BY secuencia DESC LIMIT 1;
    NEW.secuencia := COALESCE(cabeza.secuencia, 0) + 1;
    NEW.hash_anterior := COALESCE(cabeza.hash, repeat('0', 64));
    NEW.momento := clock_timestamp();
    NEW.hash := public.bitacora_hash(NEW.hash_anterior, public.bitacora_canonico(NEW));
    PERFORM set_config('app.fase', COALESCE(v_fase, ''), true);
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION bitacora_encadenar() FROM PUBLIC;
CREATE TRIGGER bitacora_encadenar BEFORE INSERT ON bitacora_evento
    FOR EACH ROW EXECUTE FUNCTION bitacora_encadenar();

CREATE OR REPLACE FUNCTION bitacora_solo_insercion() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'La bitácora es de solo inserción: nadie modifica ni borra eventos (RG1, Arquitectura §4.5).'
        USING ERRCODE = 'insufficient_privilege';
END
$$;
CREATE TRIGGER bitacora_solo_insercion BEFORE UPDATE OR DELETE ON bitacora_evento
    FOR EACH ROW EXECUTE FUNCTION bitacora_solo_insercion();

-- Verificación: recalcula cada hash y cada enlace; devuelve los problemas (vacío = íntegra).
-- SECURITY INVOKER: corre con los permisos de quien la llama (GC o AE leen toda la tabla).
CREATE OR REPLACE FUNCTION bitacora_verificar()
    RETURNS TABLE (secuencia bigint, problema text)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    e public.bitacora_evento;
    anterior_hash text := repeat('0', 64);
    esperada bigint := 1;
BEGIN
    FOR e IN SELECT * FROM public.bitacora_evento ORDER BY bitacora_evento.secuencia LOOP
        IF e.secuencia <> esperada THEN
            secuencia := esperada; problema := 'falta el evento (hueco en la secuencia)';
            RETURN NEXT;
            esperada := e.secuencia;
            anterior_hash := e.hash_anterior;
        END IF;
        IF e.hash_anterior <> anterior_hash THEN
            secuencia := e.secuencia; problema := 'el enlace con el evento anterior no coincide';
            RETURN NEXT;
        END IF;
        IF e.hash <> public.bitacora_hash(e.hash_anterior, public.bitacora_canonico(e)) THEN
            secuencia := e.secuencia; problema := 'el contenido no coincide con su hash (alterado)';
            RETURN NEXT;
        END IF;
        anterior_hash := e.hash;
        esperada := e.secuencia + 1;
    END LOOP;
END
$$;

ALTER TABLE bitacora_evento ENABLE ROW LEVEL SECURITY;
ALTER TABLE bitacora_evento FORCE ROW LEVEL SECURITY;
CREATE POLICY evento_sistema_lectura ON bitacora_evento FOR SELECT USING (app_fase() = 'sistema');
CREATE POLICY evento_sistema_alta ON bitacora_evento FOR INSERT WITH CHECK (app_fase() = 'sistema');
CREATE POLICY evento_interna ON bitacora_evento FOR SELECT
    USING (app_fase() = 'bitacora' AND current_user <> session_user);
CREATE POLICY evento_lectura ON bitacora_evento FOR SELECT
    USING (CASE WHEN app_fase() = 'bitacora' THEN false
                ELSE ({NO_PREINGRESO} AND app_sujeto() IS NOT NULL
                      AND (app_tiene_rol('GC') OR app_tiene_rol('AE'))) END);
CREATE POLICY evento_alta ON bitacora_evento FOR INSERT
    WITH CHECK (actor_id IS NULL OR actor_id = app_sujeto());

DO $$
BEGIN
    IF to_regrole('app_runtime') IS NOT NULL THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON bitacora_evento FROM app_runtime;
        GRANT SELECT, INSERT ON bitacora_evento TO app_runtime;
    END IF;
END
$$;
"""

REVERSO = """
DROP POLICY IF EXISTS evento_alta ON bitacora_evento;
DROP POLICY IF EXISTS evento_lectura ON bitacora_evento;
DROP POLICY IF EXISTS evento_interna ON bitacora_evento;
DROP POLICY IF EXISTS evento_sistema_alta ON bitacora_evento;
DROP POLICY IF EXISTS evento_sistema_lectura ON bitacora_evento;
ALTER TABLE bitacora_evento NO FORCE ROW LEVEL SECURITY;
ALTER TABLE bitacora_evento DISABLE ROW LEVEL SECURITY;
DROP FUNCTION IF EXISTS bitacora_verificar();
DROP TRIGGER IF EXISTS bitacora_solo_insercion ON bitacora_evento;
DROP FUNCTION IF EXISTS bitacora_solo_insercion();
DROP TRIGGER IF EXISTS bitacora_encadenar ON bitacora_evento;
DROP FUNCTION IF EXISTS bitacora_encadenar();
DROP FUNCTION IF EXISTS bitacora_hash(text, text);
DROP FUNCTION IF EXISTS bitacora_canonico(bitacora_evento);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("bitacora", "0001_initial"),
        ("cuentas", "0024_rls_tablas_django"),  # app_sujeto(), app_fase(), app_tiene_rol()
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)]
