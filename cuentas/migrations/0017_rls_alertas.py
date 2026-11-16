"""
RLS de las alertas (O9, C22).

  - Lectura: GC y AE (matriz O9 L) y el propio sujeto, porque el PEP revisa en
    su petición si está suspendido por H1. La matriz sigue negando a los demás
    roles la operación L de O9 en pantalla.
  - Alta: el motor HBAC crea la alerta en la petición del sujeto que la provoca,
    así que solo se puede crear sobre uno mismo, ABIERTA y sin resolución (o en
    'sistema'). Nadie crea alertas sobre otra persona.
  - Resolución: un GC distinto del sujeto (V14), a su nombre, con motivo.
  - Resuelta, no cambia; nadie cambia su sujeto, control ni detalle (disparador).
"""

from django.db import migrations

SQL = """
ALTER TABLE cuentas_alerta ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuentas_alerta FORCE ROW LEVEL SECURITY;
CREATE POLICY alerta_lectura ON cuentas_alerta FOR SELECT
    USING (app_tiene_rol('GC') OR app_tiene_rol('AE')
           OR (sujeto_id = app_sujeto() AND app_fase() IS DISTINCT FROM 'preingreso'));
CREATE POLICY alerta_alta ON cuentas_alerta FOR INSERT
    WITH CHECK (sujeto_id = app_sujeto() AND app_fase() IS DISTINCT FROM 'preingreso'
                AND estado = 'ABIERTA' AND resuelta_por_id IS NULL);
CREATE POLICY alerta_resolucion ON cuentas_alerta FOR UPDATE
    USING (app_tiene_rol('GC') AND estado = 'ABIERTA' AND sujeto_id <> app_sujeto())
    WITH CHECK (estado = 'RESUELTA' AND resuelta_por_id = app_sujeto() AND motivo_resolucion <> '');
CREATE POLICY alerta_sistema ON cuentas_alerta FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

CREATE OR REPLACE FUNCTION cuentas_alerta_inmutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF app_fase() IS NOT DISTINCT FROM 'sistema' THEN
        RETURN NEW;
    END IF;
    IF OLD.estado <> 'ABIERTA'
        OR NEW.sujeto_id IS DISTINCT FROM OLD.sujeto_id
        OR NEW.control IS DISTINCT FROM OLD.control
        OR NEW.prioridad IS DISTINCT FROM OLD.prioridad
        OR NEW.detalle IS DISTINCT FROM OLD.detalle
        OR NEW.creada_en IS DISTINCT FROM OLD.creada_en
    THEN
        RAISE EXCEPTION 'Una alerta resuelta no cambia, ni su sujeto, control o detalle.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER alerta_inmutable BEFORE UPDATE ON cuentas_alerta
    FOR EACH ROW EXECUTE FUNCTION cuentas_alerta_inmutable();
"""

REVERSO = """
DROP TRIGGER IF EXISTS alerta_inmutable ON cuentas_alerta;
DROP FUNCTION IF EXISTS cuentas_alerta_inmutable();
DROP POLICY IF EXISTS alerta_lectura ON cuentas_alerta;
DROP POLICY IF EXISTS alerta_alta ON cuentas_alerta;
DROP POLICY IF EXISTS alerta_resolucion ON cuentas_alerta;
DROP POLICY IF EXISTS alerta_sistema ON cuentas_alerta;
ALTER TABLE cuentas_alerta NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cuentas_alerta DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0016_alertas")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
