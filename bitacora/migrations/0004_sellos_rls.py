"""
Sellos de la bitácora: solo inserción, solo los crea el proceso del sistema, RLS con FORCE.

  lectura   GC y AE (igual que la bitácora) y la fase 'sistema' (verificación diaria).
  inserción solo la fase 'sistema' (comando sellar_bitacora, Cloud Run Job horario).
Nadie actualiza ni borra (disparador, también para el dueño); app_runtime no tiene UPDATE,
DELETE ni TRUNCATE. Un sello borrado de la tabla igual queda en Cloud Logging.
"""

from django.db import migrations

NO_PREINGRESO = "app_fase() IS DISTINCT FROM 'preingreso'"

SQL = f"""
CREATE TRIGGER sello_solo_insercion BEFORE UPDATE OR DELETE ON bitacora_sellobitacora
    FOR EACH ROW EXECUTE FUNCTION bitacora_solo_insercion();

ALTER TABLE bitacora_sellobitacora ENABLE ROW LEVEL SECURITY;
ALTER TABLE bitacora_sellobitacora FORCE ROW LEVEL SECURITY;
CREATE POLICY sello_sistema_lectura ON bitacora_sellobitacora FOR SELECT USING (app_fase() = 'sistema');
CREATE POLICY sello_sistema_alta ON bitacora_sellobitacora FOR INSERT WITH CHECK (app_fase() = 'sistema');
CREATE POLICY sello_lectura ON bitacora_sellobitacora FOR SELECT
    USING ({NO_PREINGRESO} AND app_sujeto() IS NOT NULL AND (app_tiene_rol('GC') OR app_tiene_rol('AE')));

DO $$
BEGIN
    IF to_regrole('app_runtime') IS NOT NULL THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON bitacora_sellobitacora FROM app_runtime;
        GRANT SELECT, INSERT ON bitacora_sellobitacora TO app_runtime;
    END IF;
END
$$;
"""

REVERSO = """
DROP POLICY IF EXISTS sello_lectura ON bitacora_sellobitacora;
DROP POLICY IF EXISTS sello_sistema_alta ON bitacora_sellobitacora;
DROP POLICY IF EXISTS sello_sistema_lectura ON bitacora_sellobitacora;
ALTER TABLE bitacora_sellobitacora NO FORCE ROW LEVEL SECURITY;
ALTER TABLE bitacora_sellobitacora DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS sello_solo_insercion ON bitacora_sellobitacora;
"""


class Migration(migrations.Migration):
    dependencies = [("bitacora", "0003_sellos")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
