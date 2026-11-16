"""
Disparadores del catálogo: la base revalida las reglas aunque alguien se salte los
servicios (defensa en profundidad).

  catalogo_parcelaversion   C20: una versión de geometría es inmutable; solo se cierra su
                            vigencia, una vez. Las mediciones y certificados que la usaron
                            conservan exactamente esa geometría.
  catalogo_finca            el propietario y la creación no cambian; una finca de BAJA no vuelve.
  catalogo_parcela          la finca y el código no cambian; una parcela de BAJA no vuelve.
  catalogo_datospropietario los datos son siempre del mismo propietario.
  todas                     RG1: nada se borra (dominio_sin_borrado, también la usa mediciones).

Errores con ERRCODE insufficient_privilege, igual que los disparadores de cuentas.
"""

from django.db import migrations

SQL = r"""
CREATE OR REPLACE FUNCTION dominio_sin_borrado() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'RG1: en % nada se borra; las bajas son cambios de estado.', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END
$$;

CREATE OR REPLACE FUNCTION catalogo_parcelaversion_inmutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.vigente_hasta IS NOT NULL OR NEW.vigente_hasta IS NULL
       OR NEW.parcela_id <> OLD.parcela_id OR NEW.numero <> OLD.numero OR NEW.area_ha <> OLD.area_ha
       OR NEW.geometria <> OLD.geometria OR NEW.vigente_desde <> OLD.vigente_desde
       OR NEW.creada_por_id <> OLD.creada_por_id THEN
        RAISE EXCEPTION 'C20: una versión de geometría no cambia; solo se cierra su vigencia, una vez.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER parcelaversion_inmutable BEFORE UPDATE ON catalogo_parcelaversion
    FOR EACH ROW EXECUTE FUNCTION catalogo_parcelaversion_inmutable();

CREATE OR REPLACE FUNCTION catalogo_finca_cambio() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.propietario_id <> OLD.propietario_id OR NEW.creada_por_id <> OLD.creada_por_id
       OR NEW.creada_en <> OLD.creada_en THEN
        RAISE EXCEPTION 'El propietario y la creación de una finca no cambian.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.estado = 'BAJA' AND NEW.estado <> 'BAJA' THEN
        RAISE EXCEPTION 'Una finca dada de baja no vuelve a estar activa.' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER finca_cambio BEFORE UPDATE ON catalogo_finca
    FOR EACH ROW EXECUTE FUNCTION catalogo_finca_cambio();

CREATE OR REPLACE FUNCTION catalogo_parcela_cambio() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.finca_id <> OLD.finca_id OR NEW.codigo <> OLD.codigo OR NEW.creada_en <> OLD.creada_en THEN
        RAISE EXCEPTION 'La finca y el código de una parcela no cambian.' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.estado = 'BAJA' AND NEW.estado <> 'BAJA' THEN
        RAISE EXCEPTION 'Una parcela dada de baja no vuelve a estar activa.' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER parcela_cambio BEFORE UPDATE ON catalogo_parcela
    FOR EACH ROW EXECUTE FUNCTION catalogo_parcela_cambio();

CREATE OR REPLACE FUNCTION catalogo_datospropietario_cambio() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.usuario_id <> OLD.usuario_id THEN
        RAISE EXCEPTION 'Los datos personales son siempre del mismo propietario.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER datospropietario_cambio BEFORE UPDATE ON catalogo_datospropietario
    FOR EACH ROW EXECUTE FUNCTION catalogo_datospropietario_cambio();

CREATE TRIGGER finca_sin_borrado BEFORE DELETE ON catalogo_finca
    FOR EACH ROW EXECUTE FUNCTION dominio_sin_borrado();
CREATE TRIGGER parcela_sin_borrado BEFORE DELETE ON catalogo_parcela
    FOR EACH ROW EXECUTE FUNCTION dominio_sin_borrado();
CREATE TRIGGER parcelaversion_sin_borrado BEFORE DELETE ON catalogo_parcelaversion
    FOR EACH ROW EXECUTE FUNCTION dominio_sin_borrado();
CREATE TRIGGER datospropietario_sin_borrado BEFORE DELETE ON catalogo_datospropietario
    FOR EACH ROW EXECUTE FUNCTION dominio_sin_borrado();
"""

REVERSO = r"""
DROP TRIGGER IF EXISTS datospropietario_sin_borrado ON catalogo_datospropietario;
DROP TRIGGER IF EXISTS parcelaversion_sin_borrado ON catalogo_parcelaversion;
DROP TRIGGER IF EXISTS parcela_sin_borrado ON catalogo_parcela;
DROP TRIGGER IF EXISTS finca_sin_borrado ON catalogo_finca;
DROP TRIGGER IF EXISTS datospropietario_cambio ON catalogo_datospropietario;
DROP TRIGGER IF EXISTS parcela_cambio ON catalogo_parcela;
DROP TRIGGER IF EXISTS finca_cambio ON catalogo_finca;
DROP TRIGGER IF EXISTS parcelaversion_inmutable ON catalogo_parcelaversion;
DROP FUNCTION IF EXISTS catalogo_datospropietario_cambio();
DROP FUNCTION IF EXISTS catalogo_parcela_cambio();
DROP FUNCTION IF EXISTS catalogo_finca_cambio();
DROP FUNCTION IF EXISTS catalogo_parcelaversion_inmutable();
DROP FUNCTION IF EXISTS dominio_sin_borrado();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("catalogo", "0002_initial"),
        ("cuentas", "0022_rls_catalogo_periodos_asignaciones"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSO)]
