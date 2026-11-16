"""
Paso 17c · RLS de las tablas de Django y allauth que quedaban sin políticas.

  account_emailaddress, account_emailconfirmation (correo: dato personal)
      Cada quien ve y actualiza lo suyo (también antes del segundo factor, como su
      cuenta); AC, que administra las cuentas, ve y cambia todos. Nadie borra.
  django_admin_log
      Cada quien ve y anota solo sus propias acciones; nadie las cambia ni borra.
  auth_group, auth_group_permissions, auth_permission, django_content_type,
  django_migrations (metadatos que escribe `migrate`)
      La app solo lee. RLS activado SIN FORCE: el dueño que corre `migrate` (y
      crea tipos de contenido y permisos al terminar) no queda sujeto; app_runtime
      no puede escribirlos aunque tenga privilegios DML sobre todas las tablas.
  django_session: queda SIN RLS a propósito. La sesión se carga por su clave antes
      de saber quién es el sujeto (es la credencial). El control está en
      cuentas_sesion: una sesión sin registro abierto no se acepta.
'Sistema' puede todo en las tablas con FORCE.
"""

from django.db import migrations

NO_PREINGRESO = "app_fase() IS DISTINCT FROM 'preingreso'"
METADATOS = ["auth_group", "auth_group_permissions", "auth_permission", "django_content_type", "django_migrations"]
PROPIO_CORREO = "user_id = app_sujeto()"
CORREO_DE = "EXISTS (SELECT 1 FROM account_emailaddress e WHERE e.id = email_address_id)"

SQL = f"""
ALTER TABLE account_emailaddress ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_emailaddress FORCE ROW LEVEL SECURITY;
CREATE POLICY correo_lectura ON account_emailaddress FOR SELECT
    USING ({PROPIO_CORREO} OR (app_tiene_rol('AC') AND {NO_PREINGRESO}));
CREATE POLICY correo_alta ON account_emailaddress FOR INSERT
    WITH CHECK ({PROPIO_CORREO} OR (app_tiene_rol('AC') AND {NO_PREINGRESO}));
CREATE POLICY correo_cambio ON account_emailaddress FOR UPDATE
    USING ({PROPIO_CORREO} OR (app_tiene_rol('AC') AND {NO_PREINGRESO}))
    WITH CHECK ({PROPIO_CORREO} OR (app_tiene_rol('AC') AND {NO_PREINGRESO}));
CREATE POLICY correo_sistema ON account_emailaddress FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

ALTER TABLE account_emailconfirmation ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_emailconfirmation FORCE ROW LEVEL SECURITY;
CREATE POLICY confirmacion_lectura ON account_emailconfirmation FOR SELECT USING ({CORREO_DE});
CREATE POLICY confirmacion_alta ON account_emailconfirmation FOR INSERT WITH CHECK ({CORREO_DE});
CREATE POLICY confirmacion_cambio ON account_emailconfirmation FOR UPDATE
    USING ({CORREO_DE}) WITH CHECK ({CORREO_DE});
CREATE POLICY confirmacion_sistema ON account_emailconfirmation FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

ALTER TABLE django_admin_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE django_admin_log FORCE ROW LEVEL SECURITY;
CREATE POLICY bitacora_admin_lectura ON django_admin_log FOR SELECT
    USING (user_id = app_sujeto() AND {NO_PREINGRESO});
CREATE POLICY bitacora_admin_alta ON django_admin_log FOR INSERT
    WITH CHECK (user_id = app_sujeto() AND {NO_PREINGRESO});
CREATE POLICY bitacora_admin_sistema ON django_admin_log FOR ALL
    USING (app_fase() = 'sistema') WITH CHECK (app_fase() = 'sistema');

{"".join(f'''ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {t}_lectura ON {t} FOR SELECT USING (true);
''' for t in METADATOS)}
"""

POLITICAS = {
    "account_emailaddress": ["correo_lectura", "correo_alta", "correo_cambio", "correo_sistema"],
    "account_emailconfirmation": ["confirmacion_lectura", "confirmacion_alta", "confirmacion_cambio",
                                  "confirmacion_sistema"],
    "django_admin_log": ["bitacora_admin_lectura", "bitacora_admin_alta", "bitacora_admin_sistema"],
    **{t: [f"{t}_lectura"] for t in METADATOS},
}

REVERSO = "".join(
    "".join(f"DROP POLICY IF EXISTS {p} ON {t};\n" for p in politicas)
    + f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;\nALTER TABLE {t} DISABLE ROW LEVEL SECURITY;\n"
    for t, politicas in POLITICAS.items()
)


class Migration(migrations.Migration):
    dependencies = [
        ("cuentas", "0023_rls_mediciones_enmiendas"),
        ("account", "0009_emailaddress_unique_primary_email"),
        ("admin", "0003_logentry_add_action_flag_choices"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("sessions", "0001_initial"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)]
