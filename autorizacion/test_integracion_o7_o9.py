"""
O7–O9 de punta a punta con el gancho autorizar() conectado al motor
real (la configuración por defecto desde este paso): los servicios de cuentas,
roles, ventanas, dispositivos, segundo factor y alertas. Incluye V9, V10 y V14.
"""

import unittest

try:
    import django  # noqa: F401
except ImportError:  # imagen del autorizador: solo corre las pruebas puras del motor
    raise unittest.SkipTest("Django no está instalado en esta imagen")

import base64
import os
import secrets
from datetime import timedelta
from unittest import mock

from allauth.mfa.totp.internal.auth import TOTP
from django.test import TestCase, override_settings
from django.utils import timezone

from autorizacion import cliente
from autorizacion.test_cliente import LOCAL, MotorFalso, con_rol
from cuentas import services
from cuentas.models import EstadoCuenta, SolicitudRol
from cuentas.selectores import hoy, roles_vigentes
from cuentas.test_dispositivos import ed25519_pem
from cuentas.test_gestion import EVENTOS, ultimo
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, FueraDeAlcance, ReautenticacionRequerida

GANCHOS = {"autorizar": "autorizacion.cliente.autorizar", "usuarios_con_rol": "cuentas.roles.usuarios_con_rol",
           "registrar_evento": "cuentas.test_gestion.registrar"}


@override_settings(DEBUG=True, VERDEAZUL_GANCHOS=GANCHOS)
@mock.patch.dict(os.environ, LOCAL)
class MotorRealBase(TestCase):
    def setUp(self):
        EVENTOS.clear()
        parche = MotorFalso().instalar()
        parche.start()
        self.addCleanup(parche.stop)
        self.ac = con_rol("admin_ac", "AC")
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")
        self.tc = con_rol("tecnica", "TC")
        self.pf = con_rol("propietaria", "PF")


class CuentasYRolesTests(MotorRealBase):
    def test_c15(self):
        nueva = services.crear_cuenta(self.ac, username="nueva", rol="TC")
        self.assertEqual(nueva.estado, EstadoCuenta.ACTIVA)
        with self.assertRaises(AccesoDenegado):
            services.crear_cuenta(self.g1, username="otra", rol="PF")      # RG1: solo AC
        with self.assertRaises(AccesoDenegado):
            services.modificar_cuenta(self.ac, self.ac, first_name="Yo")    # C15: u ≠ s
        self.assertEqual(ultimo("cuenta:modificar")["resultado"], "DENEGADO")
        services.dar_de_baja(self.ac, nueva)

    def test_v10_y_c7(self):
        with self.assertRaises(AccesoDenegado):
            services.asignar_rol(self.ac, self.tc, "VF")                    # TC ⊕ VF
        with self.assertRaises(AccesoDenegado):
            services.asignar_rol(self.ac, self.ac, "GC")                    # a sí mismo
        solicitud = services.asignar_rol(self.ac, self.pf, "GC")
        with self.assertRaises(AccesoDenegado):
            services.autorizar_solicitud(self.ac, solicitud)                # AU es de GC
        services.autorizar_solicitud(self.g1, solicitud)
        self.assertEqual(roles_vigentes(self.pf), {"PF", "GC"})

    def test_c7_e_se_reevalua_al_autorizar(self):
        solicitud = services.asignar_rol(self.ac, self.pf, "GC")
        services.asignar_rol(self.ac, self.pf, "AC")
        with self.assertRaises(AccesoDenegado):
            services.autorizar_solicitud(self.g1, solicitud)
        self.assertEqual(SolicitudRol.objects.get(pk=solicitud.pk).estado, "SOLICITADA")


class VentanaYAuditorTests(MotorRealBase):
    def setUp(self):
        super().setUp()
        self.ae = services.crear_cuenta(self.ac, username="auditora", rol="AE", inicio=hoy(),
                                        fin=hoy() + timedelta(days=5))

    def test_c14_y_c5(self):
        with self.assertRaises(AccesoDenegado):
            services.aprobar_ventana(self.ac, self.ae.ventana_auditoria)    # RG1: AU es de GC
        services.aprobar_ventana(self.g1, self.ae.ventana_auditoria)
        for accion in (Accion.MEDICION_LEER, Accion.CERTIFICADO_EXPORTAR, Accion.BITACORA_LEER):
            with self.subTest(accion=accion):
                cliente.autorizar(self.ae, accion, None, {"registros": 10})
        with self.assertRaises(AccesoDenegado):                             # V9: otra operación
            cliente.autorizar(self.ae, Accion.MEDICION_DEVOLVER)

    def test_v9_un_dia_despues_del_fin(self):
        services.aprobar_ventana(self.g1, self.ae.ventana_auditoria)
        with mock.patch("cuentas.selectores.hoy", return_value=hoy() + timedelta(days=6)), \
                self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.ae, Accion.MEDICION_LEER)

    def test_sin_aprobar_no_lee(self):
        services.cambiar_estado(self.ae, EstadoCuenta.ACTIVA)  # aunque la cuenta estuviera activa
        services.agregar_rol(self.ae, "AE")
        with self.assertRaises(AccesoDenegado):
            cliente.autorizar(self.ae, Accion.MEDICION_LEER)


class DispositivosFactorYAlertasTests(MotorRealBase):
    def test_c18(self):
        services.registrar_dispositivo(self.ac, self.tc, etiqueta="Tableta", llave_publica=ed25519_pem())
        with self.assertRaises(AccesoDenegado):
            services.registrar_dispositivo(self.ac, self.pf, etiqueta="X", llave_publica=ed25519_pem())
        with self.assertRaises(AccesoDenegado):                             # RG1: CR de O7c es de AC
            services.registrar_dispositivo(self.g1, self.tc, etiqueta="Y", llave_publica=ed25519_pem())

    def test_c17(self):
        r = services.solicitar_restablecimiento(self.ac, self.tc, motivo="Perdió el teléfono")
        with self.assertRaises(AccesoDenegado):
            services.solicitar_restablecimiento(self.ac, self.ac, motivo="El mío")
        with self.assertRaises(AccesoDenegado):                             # AU es de GC
            services.autorizar_restablecimiento(self.ac, r, metodo_verificacion="PRESENCIAL",
                                                verificado_en=timezone.now())
        services.autorizar_restablecimiento(self.g1, r, metodo_verificacion="PRESENCIAL",
                                            verificado_en=timezone.now())

    def test_c21(self):
        cliente.autorizar(self.tc, Accion.CUENTA_LEER, self.tc)
        with self.assertRaises(FueraDeAlcance):
            cliente.autorizar(self.tc, Accion.CUENTA_LEER, self.pf)
        cliente.autorizar(self.tc, Accion.FACTOR_REGISTRAR, self.tc)        # primer factor
        TOTP.activate(self.tc, base64.b32encode(secrets.token_bytes(20)).decode())
        with self.assertRaises(ReautenticacionRequerida):                   # el segundo exige mfa
            cliente.autorizar(self.tc, Accion.FACTOR_REGISTRAR, self.tc)

    def test_c22_y_v14(self):
        propia, _ = services.crear_alerta(self.g1, "H2")
        ajena, _ = services.crear_alerta(self.tc, "H1")
        with self.assertRaises(AccesoDenegado):
            services.resolver_alerta(self.g1, propia, motivo="es mía")      # V14
        services.resolver_alerta(self.g2, propia, motivo="Revisado")
        services.resolver_alerta(self.g1, ajena, motivo="Revisado")
        with self.assertRaises(AccesoDenegado):                             # O9 en pantalla: no para TC
            cliente.autorizar(self.tc, Accion.ALERTA_LEER, ajena)
