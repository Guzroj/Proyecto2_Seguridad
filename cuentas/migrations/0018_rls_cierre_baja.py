"""
RLS del cierre de la baja de un técnico (C16, sincronización final).

  - cuentas_usuario: un GC pasa una cuenta ajena de EN_BAJA a INACTIVA (y nada más).
  - cuentas_dispositivocampo: un GC revoca, a su nombre, los dispositivos
    activos de un técnico cuya cuenta está EN_BAJA.
Las sesiones ya las puede cerrar un GC (0009).
"""

from django.db import migrations

SQL = """
CREATE POLICY usuario_cierre_baja_gc ON cuentas_usuario FOR UPDATE
    USING (app_tiene_rol('GC') AND id <> app_sujeto() AND estado = 'EN_BAJA'
           AND NOT is_superuser AND NOT is_staff)
    WITH CHECK (id <> app_sujeto() AND estado = 'INACTIVA' AND NOT is_active
                AND NOT is_superuser AND NOT is_staff);

CREATE POLICY dispositivo_revocacion_gc ON cuentas_dispositivocampo FOR UPDATE
    USING (app_tiene_rol('GC') AND estado = 'ACTIVO' AND EXISTS (
        SELECT 1 FROM cuentas_usuario u WHERE u.id = cuentas_dispositivocampo.responsable_id
           AND u.estado = 'EN_BAJA'))
    WITH CHECK (estado = 'REVOCADO' AND revocado_por_id = app_sujeto());
"""

REVERSO = """
DROP POLICY IF EXISTS dispositivo_revocacion_gc ON cuentas_dispositivocampo;
DROP POLICY IF EXISTS usuario_cierre_baja_gc ON cuentas_usuario;
"""


class Migration(migrations.Migration):
    dependencies = [("cuentas", "0017_rls_alertas")]

    operations = [migrations.RunSQL(SQL, REVERSO)]
