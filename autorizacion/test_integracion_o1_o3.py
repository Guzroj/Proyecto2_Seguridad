"""
Paso 14a · O1–O3 de punta a punta: objetos reales creados con los servicios del
dominio, atributos leídos de la base por el PEP y decisión del motor.
Incluye V1, V16 y V17.
"""

import unittest

try:
    import django  # noqa: F401
except ImportError:  # imagen del autorizador: solo corre las pruebas puras del motor
    raise unittest.SkipTest("Django no está instalado en esta imagen")

import os
from datetime import date
from unittest import mock

from django.test import TestCase, override_settings

from autorizacion import pep, reglas
from autorizacion.reglas import CONCEDE, FUERA_DE_ALCANCE, NIEGA
from autorizacion.test_cliente import LOCAL, MotorFalso, con_rol
from catalogo import services as catalogo
from cuentas import services as cuentas
from cuentas.test_gestion import PERMISIVO
from mediciones import services as mediciones
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado

def poligono(n: int = 0) -> dict:
    """Un triángulo distinto por n, separados entre sí: C20 rechaza parcelas que se traslapan."""
    x = -84.0 + 0.02 * n
    return {"type": "Polygon", "coordinates": [[[x, 10.0], [x, 10.01], [x + 0.01, 10.01], [x, 10.0]]]}


POLIGONO = poligono(0)


def decidir(usuario, accion, recurso=None, contexto=None):
    return reglas.decidir(pep.construir_solicitud(usuario, accion, recurso, contexto))


@PERMISIVO
class EscenarioBase(TestCase):
    """Finca de pf con una parcela, período abierto, tc asignado y una medición enviada."""

    def setUp(self):
        self.ac = con_rol("admin_ac", "AC")
        self.gc = con_rol("gerente", "GC")
        self.tc = con_rol("tecnica", "TC")
        self.tc2 = con_rol("tecnico2", "TC")
        self.pf = con_rol("propietaria", "PF")
        self.pf2 = con_rol("propietario2", "PF")
        self.vf = con_rol("verificador", "VF")
        self.vf2 = con_rol("verificador2", "VF")
        self.finca = catalogo.crear_finca(self.ac, "La Esperanza", "San Carlos", self.pf)
        self.parcela = catalogo.crear_parcela(self.ac, self.finca, codigo="P1", area_ha="1.5", geometria=POLIGONO)
        self.periodo = mediciones.abrir_periodo(self.gc, "2026-A", date(2026, 1, 1), date(2026, 6, 30))
        self.asignacion = mediciones.crear_asignacion(self.ac, self.tc, self.finca, self.periodo)
        self.medicion = mediciones.crear_medicion(
            self.tc, self.parcela, self.periodo, numero_arboles=120, dap_promedio_cm="22.5", altura_promedio_m="11")

    def enviar(self):
        with mock.patch("mediciones.services._azar.choice", side_effect=lambda c: next(u for u in c if u == self.vf)):
            self.medicion = mediciones.enviar_medicion(self.tc, self.medicion)

    def assertDecision(self, decision, resultado, regla=None):
        self.assertEqual(decision.resultado, resultado, decision.motivo)
        if regla:
            self.assertEqual(decision.regla, regla)


class LecturasTests(EscenarioBase):
    def test_c6_y_v1_tc_solo_ve_la_finca_asignada(self):
        for recurso, accion in ((self.finca, Accion.FINCA_LEER), (self.parcela, Accion.PARCELA_LEER)):
            with self.subTest(accion=accion):
                self.assertDecision(decidir(self.tc, accion, recurso), CONCEDE, "C6")
                self.assertDecision(decidir(self.tc2, accion, recurso), FUERA_DE_ALCANCE, "C6")  # V1
        mediciones.terminar_asignacion(self.ac, self.asignacion)
        self.assertDecision(decidir(self.tc, Accion.FINCA_LEER, self.finca), FUERA_DE_ALCANCE)

    def test_c8_tc_ve_sus_asignaciones(self):
        self.assertDecision(decidir(self.tc, Accion.ASIGNACION_LEER, self.asignacion), CONCEDE, "C8")
        self.assertDecision(decidir(self.tc2, Accion.ASIGNACION_LEER, self.asignacion), FUERA_DE_ALCANCE)

    def test_c9_vf_ve_lo_que_verifica(self):
        self.assertDecision(decidir(self.vf, Accion.FINCA_LEER, self.finca), FUERA_DE_ALCANCE)
        self.enviar()
        for recurso, accion in ((self.medicion, Accion.MEDICION_LEER), (self.finca, Accion.FINCA_LEER)):
            with self.subTest(accion=accion):
                self.assertDecision(decidir(self.vf, accion, recurso), CONCEDE, "C9")
                self.assertDecision(decidir(self.vf2, accion, recurso), FUERA_DE_ALCANCE, "C9")

    def test_c4_pf_ve_lo_suyo(self):
        datos = catalogo.registrar_datos_propietario(self.ac, self.pf, nombre_completo="Patricia Fallas",
                                                     identificacion="1-1111-1111")
        for recurso, accion in ((self.finca, Accion.FINCA_LEER), (self.parcela, Accion.PARCELA_LEER),
                                (datos, Accion.PROPIETARIO_LEER)):
            with self.subTest(accion=accion):
                self.assertDecision(decidir(self.pf, accion, recurso), CONCEDE, "C4")
                self.assertDecision(decidir(self.pf2, accion, recurso), FUERA_DE_ALCANCE, "C4")
        self.enviar()
        self.assertDecision(decidir(self.pf, Accion.MEDICION_LEER, self.medicion), FUERA_DE_ALCANCE)  # aún no APROBADA

    def test_lecturas_sin_condicion(self):
        for usuario in (self.tc, self.vf, self.gc, self.ac):
            with self.subTest(usuario=usuario.username):
                self.assertDecision(decidir(usuario, Accion.PERIODO_LEER, self.periodo), CONCEDE)
        self.assertDecision(decidir(self.gc, Accion.ASIGNACION_LEER, self.asignacion), CONCEDE)

    def test_no_viajan_datos_personales(self):
        catalogo.registrar_datos_propietario(self.ac, self.pf, nombre_completo="Patricia Fallas",
                                             identificacion="1-1111-1111", correo="pf@example.com")
        texto = str(pep.construir_solicitud(self.ac, Accion.FINCA_MODIFICAR, self.finca,
                                            {"propietario": self.pf, "datos": self.pf.datos_propietario}))
        for dato in ("Patricia", "1-1111-1111", "pf@example.com", "La Esperanza", "San Carlos", "propietaria"):
            self.assertNotIn(dato, texto)


class EscriturasTests(EscenarioBase):
    def test_c13_y_v16(self):
        self.assertDecision(decidir(self.gc, Accion.PERIODO_CERRAR, self.periodo), NIEGA, "C13")  # aún ABIERTO
        self.assertDecision(decidir(self.gc, Accion.PERIODO_PASAR_VERIFICACION, self.periodo), CONCEDE, "C13")
        self.enviar()
        mediciones.pasar_a_verificacion(self.gc, self.periodo)
        self.periodo.refresh_from_db()
        self.assertDecision(decidir(self.gc, Accion.PERIODO_CERRAR, self.periodo), NIEGA, "C13")  # V16: ENVIADA
        self.assertDecision(decidir(self.gc, Accion.PERIODO_PASAR_VERIFICACION, self.periodo), NIEGA, "C13")

    def test_c19_y_v17(self):
        contexto = {"tecnico": self.tc2, "finca": self.finca, "periodo": self.periodo}
        self.assertDecision(decidir(self.ac, Accion.ASIGNACION_CREAR, None, contexto), CONCEDE, "C19")
        duena = con_rol("duena_tecnica", "TC", "PF")
        finca2 = catalogo.crear_finca(self.ac, "El Roble", "Sarapiquí", duena)
        v17 = {"tecnico": duena, "finca": finca2, "periodo": self.periodo}
        self.assertDecision(decidir(self.ac, Accion.ASIGNACION_CREAR, None, v17), NIEGA, "C19")
        sin_tc = {"tecnico": self.pf2, "finca": self.finca, "periodo": self.periodo}
        self.assertDecision(decidir(self.ac, Accion.ASIGNACION_CREAR, None, sin_tc), NIEGA, "C19")
        self.assertDecision(decidir(self.ac, Accion.ASIGNACION_TERMINAR, self.asignacion), CONCEDE, "C19")
        cuentas.terminar_rol(self.tc, "TC")  # quien perdió el rol puede quedar sin asignación
        self.assertDecision(decidir(self.ac, Accion.ASIGNACION_TERMINAR, self.asignacion), CONCEDE, "C19")

    def test_c20_lo_dado_de_baja_no_se_toca(self):
        self.assertDecision(decidir(self.ac, Accion.FINCA_MODIFICAR, self.finca), CONCEDE, "C20")
        self.assertDecision(decidir(self.ac, Accion.PARCELA_MODIFICAR_GEOMETRIA, self.parcela), CONCEDE, "C20")
        catalogo.dar_baja_finca(self.ac, self.finca)
        self.finca.refresh_from_db()
        self.parcela.refresh_from_db()
        for recurso, accion in ((self.finca, Accion.FINCA_MODIFICAR), (self.finca, Accion.PARCELA_CREAR),
                                (self.parcela, Accion.PARCELA_MODIFICAR_GEOMETRIA)):
            with self.subTest(accion=accion):
                self.assertDecision(decidir(self.ac, accion, recurso), NIEGA, "C20")


@override_settings(DEBUG=True)
@mock.patch.dict(os.environ, LOCAL)
class ConMotorRealTests(EscenarioBase):
    """Los servicios del dominio con el gancho autorizar() conectado al motor por HTTP."""

    def motor(self):
        ganchos = {"autorizar": "autorizacion.cliente.autorizar", "usuarios_con_rol": "cuentas.roles.usuarios_con_rol"}
        return override_settings(VERDEAZUL_GANCHOS=ganchos)

    def test_v17_ac_no_asigna_a_un_tecnico_su_propia_finca(self):
        duena = con_rol("duena_tecnica", "TC", "PF")
        finca2 = catalogo.crear_finca(self.ac, "El Roble", "Sarapiquí", duena)
        with self.motor(), MotorFalso().instalar():
            mediciones.crear_asignacion(self.ac, self.tc2, self.finca, self.periodo)
            with self.assertRaises(AccesoDenegado):
                mediciones.crear_asignacion(self.ac, duena, finca2, self.periodo)
            with self.assertRaises(AccesoDenegado):  # GC no asigna (RG1)
                mediciones.crear_asignacion(self.gc, self.tc2, finca2, self.periodo)

    def test_periodos_y_catalogo(self):
        with self.motor(), MotorFalso().instalar():
            with self.assertRaises(AccesoDenegado):
                mediciones.cerrar_periodo(self.gc, self.periodo)
            mediciones.pasar_a_verificacion(self.gc, self.periodo)
            catalogo.modificar_finca(self.ac, self.finca, nombre="La Esperanza Norte")
            catalogo.dar_baja_finca(self.ac, self.finca)
            with self.assertRaises(AccesoDenegado):
                catalogo.modificar_finca(self.ac, self.finca, nombre="Otra")

    def test_c20_o1b_ac_crea_y_luego_modifica_datos_del_propietario(self):
        """AC·O1b: la primera vez se autoriza como CR y las siguientes como MO; GC y TC no tienen O1b."""
        from nucleo import ganchos

        with self.motor(), MotorFalso().instalar(), \
                mock.patch("nucleo.ganchos.autorizar", wraps=ganchos.autorizar) as espia:
            for intruso in (self.gc, self.tc):  # GC·O1b y TC·O1b: "—" (RG1)
                with self.subTest(rol=intruso.username), self.assertRaises(AccesoDenegado):
                    catalogo.registrar_datos_propietario(intruso, self.pf, nombre_completo="X", identificacion="0-0")
            datos = catalogo.registrar_datos_propietario(self.ac, self.pf, nombre_completo="Patricia Fallas",
                                                         identificacion="1-1111-1111")
            self.assertEqual(espia.call_args.args[1], Accion.PROPIETARIO_CREAR_DATOS)
            catalogo.registrar_datos_propietario(self.ac, self.pf, nombre_completo="Patricia Fallas Mora",
                                                 identificacion="1-1111-1111")
            self.assertEqual(espia.call_args.args[1], Accion.PROPIETARIO_REGISTRAR_DATOS)
        datos.refresh_from_db()
        self.assertEqual(datos.nombre_completo, "Patricia Fallas Mora")
