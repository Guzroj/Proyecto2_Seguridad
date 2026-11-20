"""
Paso 17a · RLS de catálogo (O1a, O1b), períodos (O2) y asignaciones (O3).

Lectura por FINCA RELACIONADA, no celda por celda: el PEP y los servicios del
dominio leen filas que la pantalla no muestra (técnicos de la finca y el período
para excluir verificadores, historial de V5, conflictos de C19). Si RLS las
ocultara, una exclusión incompleta concedería por error. El alcance exacto de la
matriz lo aplica el PEP (autorizacion.listas y el 404 de cada lectura); RLS es la
red de seguridad contra consultas sin filtro.

app_ve_finca(f): GC, AC o AE (toda su columna), o el sujeto es propietario de f,
técnico con alguna asignación en f, o autor, verificador o actor de una medición
de f. SECURITY DEFINER con app.fase = 've_finca' para leer asignaciones y
mediciones sin recursión de políticas; devuelve solo un booleano sobre el sujeto
de la conexión.

  catalogo_finca, catalogo_parcela, catalogo_parcelaversion, mediciones_asignacion:
      lectura: finca relacionada; alta y cambios: AC (C20, C19). Quien lee también
      puede bloquear la fila (SELECT ... FOR UPDATE), pero solo AC la cambia.
  catalogo_datospropietario (O1b, datos personales): el propio propietario, AC y AE.
      Alta y cambios: AC.
  mediciones_periodo: TC, VF, GC, AC y AE leen (y bloquean); solo GC abre y cambia.
  Nadie borra (no hay políticas de DELETE). 'sistema': procesos sin usuario.
"""

from django.db import migrations

LECTORES_O2 = "app_tiene_rol('TC') OR app_tiene_rol('VF') OR app_tiene_rol('GC') OR app_tiene_rol('AC') OR app_tiene_rol('AE')"
NO_PREINGRESO = "app_fase() IS DISTINCT FROM 'preingreso'"


def _rls(tabla):
    return f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY;\nALTER TABLE {tabla} FORCE ROW LEVEL SECURITY;\n"


def _sistema(tabla, nombre):
    return (f"CREATE POLICY {nombre}_sistema ON {tabla} FOR ALL\n"
            f"    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');\n")


def _interna(tabla, nombre):
    """Lectura dentro de app_ve_finca (corre como su dueño)."""
    return (f"CREATE POLICY {nombre}_ve_finca ON {tabla} FOR SELECT\n"
            f"    USING (app_fase() = 've_finca' AND current_user <> session_user);\n")


SQL = f"""
-- La fase se fija en el cuerpo y se restaura al salir. No se usa SET en la
-- definición de la función: PostgreSQL exige ser superusuario para fijar un
-- GUC propio (app.*) desde CREATE FUNCTION, y el rol dueño de Neon no lo es.
CREATE OR REPLACE FUNCTION app_ve_finca(p_finca bigint) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
DECLARE
    v_fase text := current_setting('app.fase', true);
    v_ok boolean;
BEGIN
    PERFORM set_config('app.fase', 've_finca', true);
    SELECT app_sujeto() IS NOT NULL AND (
           app_tiene_rol('GC') OR app_tiene_rol('AC') OR app_tiene_rol('AE')
        OR EXISTS (SELECT 1 FROM public.catalogo_finca f
                    WHERE f.id = p_finca AND f.propietario_id = app_sujeto())
        OR EXISTS (SELECT 1 FROM public.mediciones_asignacion a
                    WHERE a.finca_id = p_finca AND a.tecnico_id = app_sujeto())
        OR EXISTS (SELECT 1 FROM public.mediciones_medicion m
                     JOIN public.catalogo_parcela p ON p.id = m.parcela_id
                    WHERE p.finca_id = p_finca
                      AND (m.autor_id = app_sujeto() OR m.verificador_id = app_sujeto()))
        OR EXISTS (SELECT 1 FROM public.mediciones_medicionactor x
                     JOIN public.mediciones_medicion m ON m.id = x.medicion_id
                     JOIN public.catalogo_parcela p ON p.id = m.parcela_id
                    WHERE p.finca_id = p_finca AND x.usuario_id = app_sujeto()))
      INTO v_ok;
    PERFORM set_config('app.fase', COALESCE(v_fase, ''), true);
    RETURN v_ok;
END
$$;

{_rls("catalogo_finca")}
{_interna("catalogo_finca", "finca")}
CREATE POLICY finca_lectura ON catalogo_finca FOR SELECT
    USING ({NO_PREINGRESO} AND app_ve_finca(id));
CREATE POLICY finca_alta ON catalogo_finca FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND creada_por_id = app_sujeto());
CREATE POLICY finca_cambio ON catalogo_finca FOR UPDATE
    USING ({NO_PREINGRESO} AND app_ve_finca(id)) WITH CHECK (app_tiene_rol('AC'));
{_sistema("catalogo_finca", "finca")}

{_rls("catalogo_parcela")}
{_interna("catalogo_parcela", "parcela")}
CREATE POLICY parcela_lectura ON catalogo_parcela FOR SELECT
    USING ({NO_PREINGRESO} AND app_ve_finca(finca_id));
CREATE POLICY parcela_alta ON catalogo_parcela FOR INSERT
    WITH CHECK (app_tiene_rol('AC'));
CREATE POLICY parcela_cambio ON catalogo_parcela FOR UPDATE
    USING ({NO_PREINGRESO} AND app_ve_finca(finca_id)) WITH CHECK (app_tiene_rol('AC'));
{_sistema("catalogo_parcela", "parcela")}

{_rls("catalogo_parcelaversion")}
CREATE POLICY parcelaversion_lectura ON catalogo_parcelaversion FOR SELECT
    USING ({NO_PREINGRESO} AND EXISTS (SELECT 1 FROM catalogo_parcela p
                                         WHERE p.id = parcela_id AND app_ve_finca(p.finca_id)));
CREATE POLICY parcelaversion_alta ON catalogo_parcelaversion FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND creada_por_id = app_sujeto());
CREATE POLICY parcelaversion_cambio ON catalogo_parcelaversion FOR UPDATE
    USING ({NO_PREINGRESO} AND EXISTS (SELECT 1 FROM catalogo_parcela p
                                         WHERE p.id = parcela_id AND app_ve_finca(p.finca_id)))
    WITH CHECK (app_tiene_rol('AC'));
{_sistema("catalogo_parcelaversion", "parcelaversion")}

{_rls("catalogo_datospropietario")}
CREATE POLICY datospropietario_lectura ON catalogo_datospropietario FOR SELECT
    USING ({NO_PREINGRESO} AND (usuario_id = app_sujeto() OR app_tiene_rol('AC') OR app_tiene_rol('AE')));
CREATE POLICY datospropietario_alta ON catalogo_datospropietario FOR INSERT
    WITH CHECK (app_tiene_rol('AC'));
CREATE POLICY datospropietario_cambio ON catalogo_datospropietario FOR UPDATE
    USING (app_tiene_rol('AC')) WITH CHECK (app_tiene_rol('AC'));
{_sistema("catalogo_datospropietario", "datospropietario")}

{_rls("mediciones_periodo")}
CREATE POLICY periodo_lectura ON mediciones_periodo FOR SELECT
    USING ({NO_PREINGRESO} AND ({LECTORES_O2}));
CREATE POLICY periodo_alta ON mediciones_periodo FOR INSERT
    WITH CHECK (app_tiene_rol('GC') AND abierto_por_id = app_sujeto());
CREATE POLICY periodo_cambio ON mediciones_periodo FOR UPDATE
    USING ({NO_PREINGRESO} AND ({LECTORES_O2})) WITH CHECK (app_tiene_rol('GC'));
{_sistema("mediciones_periodo", "periodo")}

{_rls("mediciones_asignacion")}
{_interna("mediciones_asignacion", "asignacion")}
CREATE POLICY asignacion_lectura ON mediciones_asignacion FOR SELECT
    USING ({NO_PREINGRESO} AND app_ve_finca(finca_id));
CREATE POLICY asignacion_alta ON mediciones_asignacion FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND creada_por_id = app_sujeto());
CREATE POLICY asignacion_cambio ON mediciones_asignacion FOR UPDATE
    USING ({NO_PREINGRESO} AND app_ve_finca(finca_id)) WITH CHECK (app_tiene_rol('AC'));
{_sistema("mediciones_asignacion", "asignacion")}
"""

TABLAS = {
    "catalogo_finca": ["finca_ve_finca", "finca_lectura", "finca_alta", "finca_cambio", "finca_sistema"],
    "catalogo_parcela": ["parcela_ve_finca", "parcela_lectura", "parcela_alta", "parcela_cambio", "parcela_sistema"],
    "catalogo_parcelaversion": ["parcelaversion_lectura", "parcelaversion_alta", "parcelaversion_cambio",
                                "parcelaversion_sistema"],
    "catalogo_datospropietario": ["datospropietario_lectura", "datospropietario_alta", "datospropietario_cambio",
                                  "datospropietario_sistema"],
    "mediciones_periodo": ["periodo_lectura", "periodo_alta", "periodo_cambio", "periodo_sistema"],
    "mediciones_asignacion": ["asignacion_ve_finca", "asignacion_lectura", "asignacion_alta", "asignacion_cambio",
                              "asignacion_sistema"],
}

REVERSO = "".join(
    "".join(f"DROP POLICY IF EXISTS {p} ON {t};\n" for p in politicas)
    + f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;\nALTER TABLE {t} DISABLE ROW LEVEL SECURITY;\n"
    for t, politicas in TABLAS.items()
) + "DROP FUNCTION IF EXISTS app_ve_finca(bigint);\n"


class Migration(migrations.Migration):
    dependencies = [
        ("cuentas", "0021_rls_actividad_hbac"),
        ("catalogo", "0002_initial"),
        ("mediciones", "0001_initial"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)]
