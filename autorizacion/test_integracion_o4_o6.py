"""
Paso 14b · O4–O6 de punta a punta: mediciones, verificación, enmiendas y el
cierre de la baja de un técnico (C16), con objetos reales, atributos leídos por
el PEP y decisión del motor. Incluye V2, V3, V4 y V15 (la parte de autorización).
"""

import unittest

try:
    import django  # noqa: F401
except ImportError:  # imagen del autorizador: solo corre las pruebas puras del motor
    raise unittest.SkipTest("Django no está instalado en esta imagen")

import os
from types import SimpleNamespace
from unittest import mock

from django.db import DatabaseError
from django.test import override_settings

from autorizacion import pep, reglas
from autorizacion.reglas import CONCEDE, FUERA_DE_ALCANCE, NIEGA, REAUTENTICAR
from autorizacion.test_cliente import LOCAL, MotorFalso, con_rol
from autorizacion.test_integracion_o1_o3 import EscenarioBase
from cuentas import services as cuentas
from cuentas.models import DispositivoCampo, EstadoCuenta, EstadoDispositivo, Usuario
from cuentas.test_dispositivos import ed25519_pem
from cuentas.test_gestion import EVENTOS, PERMISIVO, ultimo
from cuentas.test_rls import RLSBase
from mediciones import services as mediciones
from mediciones.models import EstadoMedicion, MotivoDevolucion
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, FueraDeAlcance, ReautenticacionRequerida, TransicionInvalida

DATOS = {"numero_arboles": 130, "dap_promedio_cm": "23", "altura_promedio_m": "12"}


def decidir(usuario, accion, recurso=None, contexto=None, mfa=None):
    solicitud = pep.construir_solicitud(usuario, accion, recurso, contexto)
    solicitud["sujeto"]["mfa_edad"] = mfa
    return reglas.decidir(solicitud)


class O4Tests(EscenarioBase):
    def test_c1_crear(self):
        ctx = {"periodo": self.periodo}
        self.assertDecision(decidir(self.tc, Accion.MEDICION_CREAR, self.parcela, ctx), CONCEDE, "C1")
        self.assertDecision(decidir(self.tc2, Accion.MEDICION_CREAR, self.parcela, ctx), FUERA_DE_ALCANCE, "C1")
        mediciones.pasar_a_verificacion(self.gc, self.periodo)
        self.periodo.refresh_from_db()
        self.assertDecision(decidir(self.tc, Accion.MEDICION_CREAR, self.parcela, ctx), NIEGA, "C1")

    def test_c1_modificar_y_enviar_y_c10_leer(self):
        for accion in (Accion.MEDICION_MODIFICAR, Accion.MEDICION_ENVIAR, Accion.MEDICION_LEER):
            with self.subTest(accion=accion):
                self.assertEqual(decidir(self.tc, accion, self.medicion).resultado, CONCEDE)
                self.assertDecision(decidir(self.tc2, accion, self.medicion), FUERA_DE_ALCANCE)
        self.enviar()
        self.assertDecision(decidir(self.tc, Accion.MEDICION_MODIFICAR, self.medicion), NIEGA, "C1")
        self.assertDecision(decidir(self.tc, Accion.MEDICION_LEER, self.medicion), CONCEDE, "C10")

    def test_v3_corrige_su_devuelta_con_el_periodo_en_verificacion(self):
        self.enviar()
        mediciones.pasar_a_verificacion(self.gc, self.periodo)
        motivo = MotivoDevolucion.objects.create(codigo="dap", descripcion="DAP fuera de rango")
        self.medicion = mediciones.devolver_medicion(self.vf, self.medicion, motivo)
        self.assertDecision(decidir(self.tc, Accion.MEDICION_MODIFICAR, self.medicion), CONCEDE, "C1")

    def test_c2_aprobar_y_v4(self):
        self.enviar()
        self.assertDecision(decidir(self.vf, Accion.MEDICION_APROBAR, self.medicion), REAUTENTICAR, "C2")
        self.assertDecision(decidir(self.vf, Accion.MEDICION_APROBAR, self.medicion, mfa=20), CONCEDE, "C2")
        self.assertDecision(decidir(self.vf2, Accion.MEDICION_APROBAR, self.medicion, mfa=20),
                            FUERA_DE_ALCANCE, "C2")  # V4
        atributos = pep.atributos_recurso(self.medicion)
        self.assertEqual((atributos["verificador"], atributos["tecnicos"]), (self.vf.pk, [self.tc.pk]))

    def test_v2_h4_una_aprobada_no_se_modifica(self):
        self.enviar()
        self.medicion = mediciones.aprobar_medicion(self.vf, self.medicion)
        self.assertDecision(decidir(self.tc, Accion.MEDICION_MODIFICAR, self.medicion), NIEGA, "H4")
        self.assertDecision(decidir(self.pf, Accion.MEDICION_LEER, self.medicion), CONCEDE, "C4")


class EnmiendaTests(EscenarioBase):
    def setUp(self):
        super().setUp()
        self.enviar()
        self.medicion = mediciones.aprobar_medicion(self.vf, self.medicion)

    def test_v15_solicitar_autorizar_y_aprobar_la_version(self):
        self.assertDecision(decidir(self.tc, Accion.ENMIENDA_SOLICITAR, self.medicion), CONCEDE, "C11")
        self.assertDecision(decidir(self.tc2, Accion.ENMIENDA_SOLICITAR, self.medicion), FUERA_DE_ALCANCE, "C11")
        enmienda = mediciones.solicitar_enmienda(self.tc, self.medicion, justificacion="Conteo incompleto", **DATOS)
        self.assertDecision(decidir(self.tc, Accion.ENMIENDA_SOLICITAR, self.medicion), NIEGA, "C11")  # en curso
        self.assertDecision(decidir(self.tc, Accion.ENMIENDA_LEER, enmienda), CONCEDE, "C12")
        self.assertDecision(decidir(self.tc2, Accion.ENMIENDA_LEER, enmienda), FUERA_DE_ALCANCE, "C12")

        self.assertDecision(decidir(self.gc, Accion.ENMIENDA_AUTORIZAR, enmienda), REAUTENTICAR, "C3")
        self.assertDecision(decidir(self.gc, Accion.ENMIENDA_AUTORIZAR, enmienda, mfa=10), CONCEDE, "C3")
        self.assertNotIn("Conteo", str(pep.atributos_recurso(enmienda)))  # nunca el texto de la justificación

        enmienda = mediciones.autorizar_enmienda(self.gc, enmienda)
        version = enmienda.version
        self.assertEqual(version.verificador, self.vf2)  # vf ya actuó en la cadena
        self.assertDecision(decidir(self.vf2, Accion.MEDICION_APROBAR, version, mfa=10), CONCEDE, "C2")
        self.assertDecision(decidir(self.vf, Accion.MEDICION_APROBAR, version, mfa=10), NIEGA, "C2")
        self.assertDecision(decidir(self.gc, Accion.ENMIENDA_AUTORIZAR, enmienda, mfa=10), NIEGA, "C3")


@PERMISIVO
class CierreBajaBase(EscenarioBase):
    def setUp(self):
        super().setUp()
        EVENTOS.clear()
        self.dispositivo = cuentas.registrar_dispositivo(self.ac, self.tc2, etiqueta="Tableta",
                                                         llave_publica=ed25519_pem())
        cuentas.dar_de_baja(self.ac, self.tc2)
        self.tc2.refresh_from_db()


class C16Tests(CierreBajaBase):
    def test_el_motor_concede_solo_al_gc_y_con_la_cuenta_en_baja(self):
        ctx = {"tecnico": self.tc2}
        self.assertDecision(decidir(self.gc, Accion.MEDICION_SINCRONIZAR, None, ctx), CONCEDE, "C16")
        self.assertDecision(decidir(self.ac, Accion.MEDICION_SINCRONIZAR, None, ctx), NIEGA, "RG1")
        self.assertDecision(decidir(self.gc, Accion.MEDICION_SINCRONIZAR, None, {"tecnico": self.tc}), NIEGA, "C16")

    def test_cierra_la_baja(self):
        tecnico = cuentas.cerrar_baja_tecnico(self.gc, self.tc2)
        self.assertEqual(tecnico.estado, EstadoCuenta.INACTIVA)
        self.dispositivo.refresh_from_db()
        self.assertEqual((self.dispositivo.estado, self.dispositivo.revocado_por),
                         (EstadoDispositivo.REVOCADO, self.gc))
        evento = ultimo("medicion:sincronizar")
        self.assertEqual(evento["actor"], self.gc)
        self.assertEqual((evento["despues"]["registros"], evento["despues"]["dispositivos_revocados"]), (0, 1))

    def test_solo_cuentas_en_baja(self):
        with self.assertRaises(TransicionInvalida):
            cuentas.cerrar_baja_tecnico(self.gc, self.tc)
        cuentas.cerrar_baja_tecnico(self.gc, self.tc2)
        with self.assertRaises(TransicionInvalida):
            cuentas.cerrar_baja_tecnico(self.gc, self.tc2)


@override_settings(DEBUG=True)
@mock.patch.dict(os.environ, LOCAL)
class ConMotorRealTests(CierreBajaBase):
    def motor(self):
        ganchos = {"autorizar": "autorizacion.cliente.autorizar", "usuarios_con_rol": "cuentas.roles.usuarios_con_rol",
                   "registrar_evento": "cuentas.test_gestion.registrar"}
        return override_settings(VERDEAZUL_GANCHOS=ganchos)

    def con_mfa(self, usuario):
        """Una petición en curso de `usuario` con segundo factor hace 10 s."""
        marca = pep.peticion_actual.set(SimpleNamespace(user=usuario, session={}))
        self.addCleanup(pep.peticion_actual.reset, marca)
        parche = mock.patch("cuentas.mfa.edad_mfa", return_value=10.0)
        parche.start()
        self.addCleanup(parche.stop)

    def test_flujo_de_una_medicion(self):
        with self.motor(), MotorFalso().instalar():
            m = mediciones.modificar_medicion(self.tc, self.medicion, numero_arboles=125)
            with self.assertRaises(FueraDeAlcance):  # otro TC activo: no es su medición
                mediciones.modificar_medicion(con_rol("tecnico3", "TC"), m, numero_arboles=1)
            with self.assertRaises(AccesoDenegado):  # tc2 está EN_BAJA (RG1)
                mediciones.modificar_medicion(self.tc2, m, numero_arboles=1)
            with mock.patch("mediciones.services._azar.choice", side_effect=lambda c: self.vf):
                m = mediciones.enviar_medicion(self.tc, m)
            with self.assertRaises(ReautenticacionRequerida):
                mediciones.aprobar_medicion(self.vf, m)
            self.con_mfa(self.vf)
            m = mediciones.aprobar_medicion(self.vf, m)
            self.assertEqual(m.estado, EstadoMedicion.APROBADA)
            with self.assertRaises(AccesoDenegado):  # V2 · H4
                mediciones.modificar_medicion(self.tc, m, numero_arboles=1)

    def test_cierre_de_baja(self):
        with self.motor(), MotorFalso().instalar():
            with self.assertRaises(AccesoDenegado):
                cuentas.cerrar_baja_tecnico(self.ac, self.tc2)
            cuentas.cerrar_baja_tecnico(self.gc, self.tc2)
        self.tc2.refresh_from_db()
        self.assertEqual(self.tc2.estado, EstadoCuenta.INACTIVA)


@PERMISIVO
class CierreBajaRLSTests(RLSBase):
    def setUp(self):
        super().setUp()
        self.ac = con_rol("admin_ac", "AC")
        self.gc = con_rol("gerente", "GC")
        self.tecnico = con_rol("tecnica", "TC")
        self.otro = con_rol("tecnico_activo", "TC")
        for tc in (self.tecnico, self.otro):
            cuentas.registrar_dispositivo(self.ac, tc, etiqueta="T", llave_publica=ed25519_pem())
        cuentas.dar_de_baja(self.ac, self.tecnico)

    def test_gc_cierra_la_baja_bajo_rls(self):
        with self.como_app(usuario_id=self.gc.pk):
            cuentas.cerrar_baja_tecnico(self.gc, self.tecnico)
        self.assertEqual(Usuario.objects.get(pk=self.tecnico.pk).estado, EstadoCuenta.INACTIVA)
        self.assertFalse(DispositivoCampo.objects.filter(responsable=self.tecnico, estado="ACTIVO").exists())

    def test_otros_roles_no(self):
        with self.como_app(usuario_id=self.beto.pk), self.assertRaises(FueraDeAlcance):  # VF
            cuentas.cerrar_baja_tecnico(self.beto, self.tecnico)

    def test_gc_no_toca_cuentas_ni_dispositivos_activos(self):
        with self.como_app(usuario_id=self.gc.pk):
            filas = self.ajeno("UPDATE cuentas_usuario SET estado = 'INACTIVA', is_active = false WHERE id = %s",
                               [self.otro.pk])
            self.assertEqual(filas, 0)
            filas = self.ajeno("UPDATE cuentas_dispositivocampo SET estado = 'REVOCADO', revocado_en = now(),"
                               " revocado_por_id = %s WHERE responsable_id = %s", [self.gc.pk, self.otro.pk])
            self.assertEqual(filas, 0)
            resultado = self.ajeno("UPDATE cuentas_usuario SET estado = 'ACTIVA', is_active = true WHERE id = %s",
                                   [self.tecnico.pk])
            self.assertIsInstance(resultado, DatabaseError)  # EN_BAJA solo pasa a INACTIVA
