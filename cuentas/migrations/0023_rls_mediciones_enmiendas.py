"""
Paso 17b · RLS de mediciones (O4), actores, enmiendas (O5) y motivos de devolución,
y la vía de proceso de la emisión del certificado.

Lecturas internas (funciones SECURITY DEFINER): en Neon el dueño de las funciones
no es superusuario, así que RLS también aplica dentro de ellas. app_fase_interna()
marca esas lecturas ('ve_finca', 'certificado'), y las políticas de lectura
normales devuelven false con un CASE dentro de ellas: así una política nunca vuelve
a llamar a app_ve_finca desde app_ve_finca (sin depender del orden en que
PostgreSQL evalúe las políticas). Se ajustan también las del paso 17a.

  mediciones_medicion: lee quien ve la finca de su parcela. Crea TC a su nombre, o
      GC la versión de una enmienda (autor = solicitante, nunca él). Cambia el autor
      TC (modificar, enviar) o el verificador asignado VF (aprobar, devolver).
  mediciones_medicionactor: lee quien ve la medición. Cada quien anota su propia
      acción; GC además hereda los actores en la versión que autoriza.
  mediciones_enmienda: lee quien ve la medición original. Solicita TC a su nombre;
      la resuelve GC a su nombre; VF la cierra al aprobar o devolver la versión que
      tiene asignada.
  mediciones_motivodevolucion: catálogo cerrado; lo leen los autenticados.
Nadie borra.

Certificado (Persona 4): la emisión corre dentro de la aprobación del VF, que no
tiene O1b. certificado_datos_propietario(m) devuelve solo nombre e identificación
del propietario, y solo si m está APROBADA y el sujeto de la conexión es su
verificador. Se usa desde autorizacion.procesos.
"""

from django.db import migrations

NO_PREINGRESO = "app_fase() IS DISTINCT FROM 'preingreso'"


def guardia(expresion: str) -> str:
    """Lectura normal: nunca dentro de una lectura interna (evita la recursión)."""
    return f"CASE WHEN app_fase_interna() THEN false ELSE ({NO_PREINGRESO} AND {expresion}) END"


VE_MEDICION = "EXISTS (SELECT 1 FROM mediciones_medicion m WHERE m.id = {col})"

# Políticas del paso 17a que se ajustan: (tabla, política, USING nuevo).
AJUSTES_17A = [
    ("catalogo_finca", "finca_lectura", guardia("app_ve_finca(id)")),
    ("catalogo_finca", "finca_cambio", guardia("app_ve_finca(id)")),
    ("catalogo_parcela", "parcela_lectura", guardia("app_ve_finca(finca_id)")),
    ("catalogo_parcela", "parcela_cambio", guardia("app_ve_finca(finca_id)")),
    ("catalogo_parcelaversion", "parcelaversion_lectura", guardia("app_ve_parcela(parcela_id)")),
    ("catalogo_parcelaversion", "parcelaversion_cambio", guardia("app_ve_parcela(parcela_id)")),
    ("mediciones_asignacion", "asignacion_lectura", guardia("app_ve_finca(finca_id)")),
    ("mediciones_asignacion", "asignacion_cambio", guardia("app_ve_finca(finca_id)")),
]
INTERNAS_17A = [("catalogo_finca", "finca_ve_finca"), ("catalogo_parcela", "parcela_ve_finca"),
                ("mediciones_asignacion", "asignacion_ve_finca")]
ORIGINAL_17A = {
    "finca_lectura": "app_fase() IS DISTINCT FROM 'preingreso' AND app_ve_finca(id)",
    "finca_cambio": "app_fase() IS DISTINCT FROM 'preingreso' AND app_ve_finca(id)",
    "parcela_lectura": "app_fase() IS DISTINCT FROM 'preingreso' AND app_ve_finca(finca_id)",
    "parcela_cambio": "app_fase() IS DISTINCT FROM 'preingreso' AND app_ve_finca(finca_id)",
    "parcelaversion_lectura": ("app_fase() IS DISTINCT FROM 'preingreso' AND EXISTS (SELECT 1 FROM catalogo_parcela p"
                               " WHERE p.id = parcela_id AND app_ve_finca(p.finca_id))"),
    "parcelaversion_cambio": ("app_fase() IS DISTINCT FROM 'preingreso' AND EXISTS (SELECT 1 FROM catalogo_parcela p"
                              " WHERE p.id = parcela_id AND app_ve_finca(p.finca_id))"),
    "asignacion_lectura": "app_fase() IS DISTINCT FROM 'preingreso' AND app_ve_finca(finca_id)",
    "asignacion_cambio": "app_fase() IS DISTINCT FROM 'preingreso' AND app_ve_finca(finca_id)",
}


def _tabla(tabla, nombre):
    return f"""
ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {tabla} FORCE ROW LEVEL SECURITY;
CREATE POLICY {nombre}_sistema ON {tabla} FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');
"""


def _interna(tabla, nombre):
    return f"CREATE POLICY {nombre}_interna ON {tabla} FOR SELECT USING (app_fase_interna());\n"


SQL = f"""
CREATE OR REPLACE FUNCTION app_fase_interna() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT app_fase() IN ('ve_finca', 'certificado') AND current_user <> session_user $$;

-- La fase se fija en el cuerpo y se restaura al salir. No se usa SET en la
-- definición de la función: PostgreSQL exige ser superusuario para fijar un
-- GUC propio (app.*) desde CREATE FUNCTION, y el rol dueño de Neon no lo es.
CREATE OR REPLACE FUNCTION app_ve_parcela(p_parcela bigint) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
DECLARE
    v_fase text := current_setting('app.fase', true);
    v_ok boolean;
BEGIN
    PERFORM set_config('app.fase', 've_finca', true);
    SELECT COALESCE((SELECT app_ve_finca(p.finca_id) FROM public.catalogo_parcela p
                      WHERE p.id = p_parcela), false) INTO v_ok;
    PERFORM set_config('app.fase', COALESCE(v_fase, ''), true);
    RETURN v_ok;
END
$$;

{"".join(f"ALTER POLICY {p} ON {t} USING ({u});" + chr(10) for t, p, u in AJUSTES_17A)}
{"".join(f"ALTER POLICY {p} ON {t} USING (app_fase_interna());" + chr(10) for t, p in INTERNAS_17A)}
CREATE POLICY datospropietario_certificado ON catalogo_datospropietario FOR SELECT
    USING (app_fase() = 'certificado' AND current_user <> session_user);

{_tabla("mediciones_medicion", "medicion")}
{_interna("mediciones_medicion", "medicion")}
CREATE POLICY medicion_lectura ON mediciones_medicion FOR SELECT
    USING ({guardia("app_ve_parcela(parcela_id)")});
CREATE POLICY medicion_alta ON mediciones_medicion FOR INSERT
    WITH CHECK ((app_tiene_rol('TC') AND autor_id = app_sujeto())
                OR (app_tiene_rol('GC') AND version_de_id IS NOT NULL AND autor_id <> app_sujeto()
                    AND estado = 'ENVIADA'));
CREATE POLICY medicion_cambio ON mediciones_medicion FOR UPDATE
    USING ({guardia("app_ve_parcela(parcela_id)")})
    WITH CHECK ((app_tiene_rol('TC') AND autor_id = app_sujeto())
                OR (app_tiene_rol('VF') AND verificador_id = app_sujeto()));

{_tabla("mediciones_medicionactor", "medicionactor")}
{_interna("mediciones_medicionactor", "medicionactor")}
CREATE POLICY medicionactor_lectura ON mediciones_medicionactor FOR SELECT
    USING ({guardia(VE_MEDICION.format(col="medicion_id"))});
CREATE POLICY medicionactor_alta ON mediciones_medicionactor FOR INSERT
    WITH CHECK ({NO_PREINGRESO} AND (usuario_id = app_sujeto()
                OR (app_tiene_rol('GC') AND accion IN ('HEREDADO', 'SOLICITAR_ENMIENDA')
                    AND EXISTS (SELECT 1 FROM mediciones_medicion v
                                 WHERE v.id = medicion_id AND v.version_de_id IS NOT NULL))));

{_tabla("mediciones_enmienda", "enmienda")}
CREATE POLICY enmienda_lectura ON mediciones_enmienda FOR SELECT
    USING ({guardia(VE_MEDICION.format(col="medicion_original_id"))});
CREATE POLICY enmienda_alta ON mediciones_enmienda FOR INSERT
    WITH CHECK (app_tiene_rol('TC') AND solicitante_id = app_sujeto());
CREATE POLICY enmienda_cambio ON mediciones_enmienda FOR UPDATE
    USING ({guardia(VE_MEDICION.format(col="medicion_original_id"))})
    WITH CHECK ((app_tiene_rol('GC') AND autorizador_id = app_sujeto())
                OR (app_tiene_rol('VF') AND EXISTS (SELECT 1 FROM mediciones_medicion v
                                                    WHERE v.id = version_id AND v.verificador_id = app_sujeto())));

{_tabla("mediciones_motivodevolucion", "motivodevolucion")}
CREATE POLICY motivodevolucion_lectura ON mediciones_motivodevolucion FOR SELECT
    USING (app_sujeto() IS NOT NULL AND {NO_PREINGRESO});

CREATE OR REPLACE FUNCTION certificado_datos_propietario(p_medicion uuid)
    RETURNS TABLE (nombre_completo text, identificacion text)
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
DECLARE
    v_fase text := current_setting('app.fase', true);
BEGIN
    PERFORM set_config('app.fase', 'certificado', true);
    RETURN QUERY
        SELECT d.nombre_completo::text, d.identificacion::text
          FROM public.mediciones_medicion m
          JOIN public.catalogo_parcela p ON p.id = m.parcela_id
          JOIN public.catalogo_finca f ON f.id = p.finca_id
          JOIN public.catalogo_datospropietario d ON d.usuario_id = f.propietario_id
         WHERE m.id = p_medicion AND m.estado = 'APROBADA' AND m.verificador_id = app_sujeto();
    PERFORM set_config('app.fase', COALESCE(v_fase, ''), true);
END
$$;
REVOKE ALL ON FUNCTION certificado_datos_propietario(uuid) FROM PUBLIC;
DO $$
BEGIN
    IF to_regrole('app_runtime') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION certificado_datos_propietario(uuid) TO app_runtime;
    END IF;
END
$$;
"""

TABLAS = {
    "mediciones_medicion": ["medicion_sistema", "medicion_interna", "medicion_lectura", "medicion_alta",
                            "medicion_cambio"],
    "mediciones_medicionactor": ["medicionactor_sistema", "medicionactor_interna", "medicionactor_lectura",
                                 "medicionactor_alta"],
    "mediciones_enmienda": ["enmienda_sistema", "enmienda_lectura", "enmienda_alta", "enmienda_cambio"],
    "mediciones_motivodevolucion": ["motivodevolucion_sistema", "motivodevolucion_lectura"],
}

REVERSO = (
    "DROP FUNCTION IF EXISTS certificado_datos_propietario(uuid);\n"
    + "".join("".join(f"DROP POLICY IF EXISTS {p} ON {t};\n" for p in politicas)
              + f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;\nALTER TABLE {t} DISABLE ROW LEVEL SECURITY;\n"
              for t, politicas in TABLAS.items())
    + "DROP POLICY IF EXISTS datospropietario_certificado ON catalogo_datospropietario;\n"
    + "".join(f"ALTER POLICY {p} ON {t} USING (app_fase() = 've_finca' AND current_user <> session_user);\n"
              for t, p in INTERNAS_17A)
    + "".join(f"ALTER POLICY {p} ON {t} USING ({ORIGINAL_17A[p]});\n" for t, p, _ in AJUSTES_17A)
    + "DROP FUNCTION IF EXISTS app_ve_parcela(bigint);\n"
    + "DROP FUNCTION IF EXISTS app_fase_interna();\n"
)


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0022_rls_catalogo_periodos_asignaciones")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
