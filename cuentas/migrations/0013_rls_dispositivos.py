"""
RLS de los dispositivos de campo (O7c, C18).

  - Lectura: AC y AE (matriz O7c L), GC (verifica las firmas en la
    sincronización final, C16) y el propio responsable (el servidor verifica
    las firmas de sus registros en su petición). La matriz sigue negando a TC
    y GC la operación L de O7c en pantalla; esto es solo lo que la base deja ver.
  - AC registra dispositivos a su nombre para otra cuenta, siempre ACTIVO.
  - AC revoca (ACTIVO -> REVOCADO, a su nombre).
  - Nadie cambia la llave, la huella, el responsable ni quién lo registró, y un
    dispositivo revocado no vuelve a ACTIVO (disparador, también en 'sistema').
"""

from django.db import migrations

SQL = """
ALTER TABLE cuentas_dispositivocampo ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuentas_dispositivocampo FORCE ROW LEVEL SECURITY;
CREATE POLICY dispositivo_lectura ON cuentas_dispositivocampo FOR SELECT
    USING (app_tiene_rol('AC') OR app_tiene_rol('AE') OR app_tiene_rol('GC')
           OR (responsable_id = app_sujeto() AND app_fase() IS DISTINCT FROM 'preingreso'));
CREATE POLICY dispositivo_alta ON cuentas_dispositivocampo FOR INSERT
    WITH CHECK (app_tiene_rol('AC') AND registrado_por_id = app_sujeto()
                AND responsable_id <> app_sujeto() AND estado = 'ACTIVO');
CREATE POLICY dispositivo_revocacion ON cuentas_dispositivocampo FOR UPDATE
    USING (app_tiene_rol('AC') AND estado = 'ACTIVO')
    WITH CHECK (estado = 'REVOCADO' AND revocado_por_id = app_sujeto());
CREATE POLICY dispositivo_sistema ON cuentas_dispositivocampo FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

CREATE OR REPLACE FUNCTION cuentas_dispositivo_inmutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.estado = 'REVOCADO'
        OR NEW.llave_publica IS DISTINCT FROM OLD.llave_publica
        OR NEW.huella IS DISTINCT FROM OLD.huella
        OR NEW.algoritmo IS DISTINCT FROM OLD.algoritmo
        OR NEW.responsable_id IS DISTINCT FROM OLD.responsable_id
        OR NEW.registrado_por_id IS DISTINCT FROM OLD.registrado_por_id
        OR NEW.registrado_en IS DISTINCT FROM OLD.registrado_en
    THEN
        RAISE EXCEPTION 'Un dispositivo solo cambia de ACTIVO a REVOCADO; su llave y responsable no cambian.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER dispositivo_inmutable BEFORE UPDATE ON cuentas_dispositivocampo
    FOR EACH ROW EXECUTE FUNCTION cuentas_dispositivo_inmutable();
"""

REVERSO = """
DROP TRIGGER IF EXISTS dispositivo_inmutable ON cuentas_dispositivocampo;
DROP FUNCTION IF EXISTS cuentas_dispositivo_inmutable();
DROP POLICY IF EXISTS dispositivo_lectura ON cuentas_dispositivocampo;
DROP POLICY IF EXISTS dispositivo_alta ON cuentas_dispositivocampo;
DROP POLICY IF EXISTS dispositivo_revocacion ON cuentas_dispositivocampo;
DROP POLICY IF EXISTS dispositivo_sistema ON cuentas_dispositivocampo;
ALTER TABLE cuentas_dispositivocampo NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cuentas_dispositivocampo DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0012_dispositivos_campo")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
