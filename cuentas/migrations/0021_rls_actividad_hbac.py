"""
RLS de los contadores HBAC (cuentas_actividadhbac).

  - Cada sujeto lee solo sus filas (el PEP cuenta n(s), μ(s) y N(s) en su petición).
  - Alta: solo sobre uno mismo y con la hora del servidor (±1 min), para que nadie
    se fabrique un historial antiguo que suba su umbral de H1.
  - Nadie modifica ni borra (no hay políticas de UPDATE ni DELETE).
  - La media del rol (μ cuando falta historial propio) sale de
    cuentas_media_lecturas_rol, SECURITY DEFINER, que devuelve solo un número:
    nadie ve las lecturas de otras personas.
  - app.fase = 'sistema': procesos sin usuario.
"""

from django.db import migrations

SQL = """
ALTER TABLE cuentas_actividadhbac ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuentas_actividadhbac FORCE ROW LEVEL SECURITY;
CREATE POLICY actividad_lectura ON cuentas_actividadhbac FOR SELECT
    USING (sujeto_id = app_sujeto() AND app_fase() IS DISTINCT FROM 'preingreso');
CREATE POLICY actividad_media ON cuentas_actividadhbac FOR SELECT
    USING (app_fase() = 'media_h1' AND current_user <> session_user);
CREATE POLICY actividad_alta ON cuentas_actividadhbac FOR INSERT
    WITH CHECK (sujeto_id = app_sujeto() AND app_fase() IS DISTINCT FROM 'preingreso'
                AND en BETWEEN statement_timestamp() - interval '1 minute'
                           AND statement_timestamp() + interval '1 minute');
CREATE POLICY actividad_sistema ON cuentas_actividadhbac FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

-- μ del rol: lecturas por hora activa, en [p_desde, p_hasta), de quienes tienen
-- hoy el rol vigente. NULL si nadie con ese rol tiene lecturas.
-- La fase se fija en el cuerpo y se restaura al salir. No se usa SET en la
-- definición de la función: PostgreSQL exige ser superusuario para fijar un
-- GUC propio (app.*) desde CREATE FUNCTION, y el rol dueño de Neon no lo es.
CREATE OR REPLACE FUNCTION cuentas_media_lecturas_rol(p_rol text, p_desde timestamptz, p_hasta timestamptz)
    RETURNS double precision
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
DECLARE
    v_fase text := current_setting('app.fase', true);
    v_media double precision;
BEGIN
    PERFORM set_config('app.fase', 'media_h1', true);
    SELECT count(*)::double precision
           / NULLIF(count(DISTINCT (a.sujeto_id, date_trunc('hour', a.en))), 0)
      INTO v_media
      FROM public.cuentas_actividadhbac a
     WHERE a.tipo = 'L' AND a.en >= p_desde AND a.en < p_hasta
       AND EXISTS (SELECT 1 FROM public.cuentas_rolusuario r
                    WHERE r.usuario_id = a.sujeto_id AND r.rol = p_rol
                      AND r.vigente_desde <= statement_timestamp()
                      AND (r.vigente_hasta IS NULL OR r.vigente_hasta > statement_timestamp()));
    PERFORM set_config('app.fase', COALESCE(v_fase, ''), true);
    RETURN v_media;
END
$$;
REVOKE ALL ON FUNCTION cuentas_media_lecturas_rol(text, timestamptz, timestamptz) FROM PUBLIC;
DO $$
BEGIN
    IF to_regrole('app_runtime') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION cuentas_media_lecturas_rol(text, timestamptz, timestamptz) TO app_runtime;
    END IF;
END
$$;
"""

REVERSO = """
DROP FUNCTION IF EXISTS cuentas_media_lecturas_rol(text, timestamptz, timestamptz);
DROP POLICY IF EXISTS actividad_lectura ON cuentas_actividadhbac;
DROP POLICY IF EXISTS actividad_media ON cuentas_actividadhbac;
DROP POLICY IF EXISTS actividad_alta ON cuentas_actividadhbac;
DROP POLICY IF EXISTS actividad_sistema ON cuentas_actividadhbac;
ALTER TABLE cuentas_actividadhbac NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cuentas_actividadhbac DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0020_actividad_hbac")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
