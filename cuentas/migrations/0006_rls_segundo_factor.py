"""
RLS del segundo factor y fase 'preingreso'.

'preingreso' = contraseña verificada, segundo factor pendiente (o login aún no
completado). En esa fase el sujeto solo ve su propia cuenta y sus factores; no
ve otras cuentas ni roles.

mfa_authenticator (O7d): cada usuario gestiona sus propios factores (C21);
'sistema' todo. La lectura de metadatos por AC/AE y el restablecimiento con
doble control (C17) agregan su política en ese paso. Los secretos van cifrados
(RG2, cuentas.adaptador.AdaptadorMFA).
"""

from django.db import migrations

LECTURA = {
    "cuentas_usuario": "usuario_lectura",
    "cuentas_rolusuario": "cuentas_rolusuario_lectura",
    "cuentas_usuario_groups": "cuentas_usuario_groups_lectura",
    "cuentas_usuario_user_permissions": "cuentas_usuario_user_permissions_lectura",
}

SQL = ""
REVERSO = ""
for tabla, politica in LECTURA.items():
    SQL += f"""
DROP POLICY {politica} ON {tabla};
CREATE POLICY {politica} ON {tabla} FOR SELECT
    USING ((app_sujeto() IS NOT NULL AND app_fase() IS DISTINCT FROM 'preingreso')
           OR app_fase() = 'sistema');
"""
    REVERSO += f"""
DROP POLICY {politica} ON {tabla};
CREATE POLICY {politica} ON {tabla} FOR SELECT
    USING (app_sujeto() IS NOT NULL OR app_fase() = 'sistema');
"""

SQL += """
CREATE POLICY usuario_propia_lectura ON cuentas_usuario FOR SELECT
    USING (id = app_sujeto());

ALTER TABLE mfa_authenticator ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfa_authenticator FORCE ROW LEVEL SECURITY;
CREATE POLICY factor_propio ON mfa_authenticator FOR ALL
    USING (user_id = app_sujeto()) WITH CHECK (user_id = app_sujeto());
CREATE POLICY factor_sistema ON mfa_authenticator FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');
"""
REVERSO += """
DROP POLICY IF EXISTS usuario_propia_lectura ON cuentas_usuario;
DROP POLICY IF EXISTS factor_propio ON mfa_authenticator;
DROP POLICY IF EXISTS factor_sistema ON mfa_authenticator;
ALTER TABLE mfa_authenticator NO FORCE ROW LEVEL SECURITY;
ALTER TABLE mfa_authenticator DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("cuentas", "0005_sesiones"),
        ("mfa", "0003_authenticator_type_uniq"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)]
