"""
Paso 10 · Dispositivos de campo (O7c): AC registra (C18) y revoca (RV).
Con ellos, la baja de un técnico con dispositivos activos lo deja EN_BAJA.
"""

import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase, override_settings

from cuentas import selectores, services, sesion
from cuentas.llaves import normalizar_llave_publica
from cuentas.models import AlgoritmoLlave, DispositivoCampo, EstadoCuenta, EstadoDispositivo
from cuentas.test_gestion import EVENTOS, PERMISIVO, ultimo
from cuentas.test_rls import RLSBase
from cuentas.test_roles_c7 import abiertas, con_rol
from cuentas.tests import crear_cuenta
from nucleo.errores import AccesoDenegado, DatoInvalido, FueraDeAlcance, TransicionInvalida

PUBLICA = serialization.PublicFormat.SubjectPublicKeyInfo
PEM = serialization.Encoding.PEM


def pem_publica(llave_privada) -> str:
    return llave_privada.public_key().public_bytes(PEM, PUBLICA).decode()


def ed25519_pem() -> str:
    return pem_publica(ed25519.Ed25519PrivateKey.generate())


def p256_pem() -> str:
    return pem_publica(ec.generate_private_key(ec.SECP256R1()))


class LlavesTests(TestCase):
    def test_acepta_ed25519_y_p256(self):
        for pem, algoritmo in ((ed25519_pem(), AlgoritmoLlave.ED25519), (p256_pem(), AlgoritmoLlave.P256)):
            with self.subTest(algoritmo=algoritmo):
                obtenido, normalizado, huella = normalizar_llave_publica("  \n" + pem + "\n ")
                self.assertEqual(obtenido, algoritmo)
                self.assertEqual(normalizado, pem)
                der = serialization.load_pem_public_key(pem.encode()).public_bytes(
                    serialization.Encoding.DER, PUBLICA)
                self.assertEqual(huella, hashlib.sha256(der).hexdigest())

    def test_rechaza_llaves_privadas(self):
        privada = ed25519.Ed25519PrivateKey.generate().private_bytes(
            PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        with self.assertRaisesMessage(DatoInvalido, "PRIVADA"):
            normalizar_llave_publica(privada)

    def test_rechaza_otros_tipos_y_basura(self):
        otros = [
            pem_publica(rsa.generate_private_key(public_exponent=65537, key_size=2048)),
            pem_publica(ec.generate_private_key(ec.SECP384R1())),
            "no es una llave",
            "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----",
            "",
            None,
            "x" * 2001,
            "-----BEGIN PUBLIC KEY-----\nñ\n-----END PUBLIC KEY-----",
        ]
        for valor in otros:
            with self.subTest(valor=str(valor)[:40]), self.assertRaises(DatoInvalido):
                normalizar_llave_publica(valor)


@PERMISIVO
class DispositivoBase(TestCase):
    def setUp(self):
        EVENTOS.clear()
        self.ac = con_rol("admin_ac", "AC")
        self.tc = con_rol("tecnica", "TC")

    def registrar(self, responsable=None, pem=None, etiqueta="Tableta 1"):
        return services.registrar_dispositivo(
            self.ac, responsable or self.tc, etiqueta=etiqueta, llave_publica=pem or ed25519_pem())


class RegistrarTests(DispositivoBase):
    def test_registra_solo_la_llave_publica(self):
        pem = p256_pem()
        d = self.registrar(pem=pem)
        self.assertEqual((d.responsable, d.estado, d.algoritmo, d.registrado_por),
                         (self.tc, EstadoDispositivo.ACTIVO, AlgoritmoLlave.P256, self.ac))
        self.assertEqual(d.llave_publica, pem)
        evento = ultimo("dispositivo:registrar")
        self.assertEqual(evento["despues"]["huella"], d.huella)
        self.assertNotIn("BEGIN PUBLIC KEY", str(evento))

    def test_c18_responsable_con_tc_vigente(self):
        for cuenta in (con_rol("propietaria", "PF"), con_rol("verificador", "VF"), self.ac):
            with self.subTest(cuenta=cuenta.username), self.assertRaises(DatoInvalido):
                self.registrar(responsable=cuenta)
        services.terminar_rol(self.tc, "TC")
        with self.assertRaises(DatoInvalido):
            self.registrar()
        self.assertFalse(DispositivoCampo.objects.exists())

    def test_c18_cuenta_del_responsable_activa(self):
        pendiente = crear_cuenta("pendiente")
        services.agregar_rol(pendiente, "TC")
        inactiva = con_rol("inactiva", "TC")
        services.dar_de_baja(self.ac, inactiva)
        for cuenta in (pendiente, inactiva):
            with self.subTest(cuenta=cuenta.username), self.assertRaises(TransicionInvalida):
                self.registrar(responsable=cuenta)

    def test_una_llave_no_se_reutiliza_nunca(self):
        pem = ed25519_pem()
        d = self.registrar(pem=pem)
        services.revocar_dispositivo(self.ac, d)
        for responsable in (self.tc, con_rol("otro_tc", "TC")):
            with self.subTest(responsable=responsable.username), self.assertRaises(DatoInvalido):
                self.registrar(responsable=responsable, pem=pem)
        self.assertEqual(DispositivoCampo.objects.count(), 1)

    def test_etiqueta_requerida(self):
        for etiqueta in ("", "   ", "x" * 101, None):
            with self.subTest(etiqueta=etiqueta), self.assertRaises(DatoInvalido):
                self.registrar(etiqueta=etiqueta)

    def test_varios_dispositivos_por_tecnico(self):
        self.registrar(etiqueta="Tableta")
        self.registrar(etiqueta="Teléfono")
        self.assertEqual(selectores.dispositivos_activos(self.tc).count(), 2)

    @override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=False)
    def test_sin_motor_se_niega(self):
        with self.assertRaises(AccesoDenegado):
            self.registrar()
        self.assertEqual(ultimo("dispositivo:registrar")["resultado"], "DENEGADO")


class RevocarTests(DispositivoBase):
    def test_revoca_definitivamente(self):
        d = services.revocar_dispositivo(self.ac, self.registrar(), motivo=" Extraviado ")
        d.refresh_from_db()
        self.assertEqual((d.estado, d.revocado_por, d.motivo_revocacion),
                         (EstadoDispositivo.REVOCADO, self.ac, "Extraviado"))
        self.assertIsNotNone(d.revocado_en)
        with self.assertRaises(TransicionInvalida):
            services.revocar_dispositivo(self.ac, d)

    def test_sin_condicion_aunque_el_tecnico_ya_no_sea_tc(self):
        d = self.registrar()
        services.terminar_rol(self.tc, "TC")
        services.revocar_dispositivo(self.ac, d)
        d.refresh_from_db()
        self.assertEqual(d.estado, EstadoDispositivo.REVOCADO)

    def test_inexistente(self):
        with self.assertRaises(FueraDeAlcance):
            services.revocar_dispositivo(self.ac, 999999)

    def test_revocar_todos_los_de_una_cuenta(self):
        self.registrar(etiqueta="A")
        self.registrar(etiqueta="B")
        ya = self.registrar(etiqueta="C")
        services.revocar_dispositivo(self.ac, ya)
        otro = self.registrar(responsable=con_rol("otro_tc", "TC"))
        self.assertEqual(services.revocar_dispositivos_de(self.tc, motivo="cierre de la baja"), 2)
        self.assertFalse(selectores.dispositivos_activos(self.tc).exists())
        self.assertIsNone(DispositivoCampo.objects.get(etiqueta="A").revocado_por)  # lo revocó un proceso
        otro.refresh_from_db()
        self.assertEqual(otro.estado, EstadoDispositivo.ACTIVO)


class BajaConDispositivosTests(DispositivoBase):
    def test_tecnico_con_dispositivo_activo_queda_en_baja(self):
        self.registrar()
        sesion.registrar_inicio(self.tc, "clave-tc")
        cuenta = services.dar_de_baja(self.ac, self.tc)
        self.assertEqual(cuenta.estado, EstadoCuenta.EN_BAJA)
        self.assertEqual(abiertas(self.tc), 0)
        # Sus dispositivos siguen activos para la sincronización final (C16).
        self.assertTrue(selectores.dispositivos_activos(self.tc).exists())

    def test_con_todos_revocados_queda_inactiva(self):
        services.revocar_dispositivo(self.ac, self.registrar())
        self.assertEqual(services.dar_de_baja(self.ac, self.tc).estado, EstadoCuenta.INACTIVA)

    def test_sin_dispositivos_queda_inactiva(self):
        self.assertEqual(services.dar_de_baja(self.ac, self.tc).estado, EstadoCuenta.INACTIVA)


class RestriccionesDeLaBaseTests(DispositivoBase):
    def test_revocacion_coherente(self):
        d = self.registrar()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DispositivoCampo.objects.filter(pk=d.pk).update(estado=EstadoDispositivo.REVOCADO)

    def test_la_llave_no_cambia_ni_vuelve_a_activo(self):
        d = self.registrar()
        with self.assertRaises(DatabaseError), transaction.atomic():
            DispositivoCampo.objects.filter(pk=d.pk).update(llave_publica=ed25519_pem())
        services.revocar_dispositivo(self.ac, d)
        with self.assertRaises(DatabaseError), transaction.atomic():
            DispositivoCampo.objects.filter(pk=d.pk).update(
                estado=EstadoDispositivo.ACTIVO, revocado_en=None, revocado_por=None)


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------
@PERMISIVO
class DispositivosRLSTests(RLSBase):
    def setUp(self):
        super().setUp()
        EVENTOS.clear()
        self.ac = con_rol("admin_ac", "AC")
        self.tc = con_rol("tecnica", "TC")
        self.tc2 = con_rol("tecnico2", "TC")
        self.gc = con_rol("gerente", "GC")

    def registrar(self, responsable):
        with self.como_app(usuario_id=self.ac.pk):
            return services.registrar_dispositivo(self.ac, responsable, etiqueta="T", llave_publica=ed25519_pem())

    def test_ac_registra_revoca_y_la_baja_queda_en_baja(self):
        d = self.registrar(self.tc)
        with self.como_app(usuario_id=self.ac.pk):
            self.assertEqual(services.dar_de_baja(self.ac, self.tc).estado, EstadoCuenta.EN_BAJA)
            services.revocar_dispositivo(self.ac, d)
        d.refresh_from_db()
        self.assertEqual(d.estado, EstadoDispositivo.REVOCADO)

    def test_quien_ve_los_dispositivos(self):
        self.registrar(self.tc)
        self.registrar(self.tc2)
        vistos = {self.ac.pk: 2, self.gc.pk: 2, self.tc.pk: 1, self.beto.pk: 0, self.ana.pk: 0}  # beto VF, ana PF
        for sujeto, esperado in vistos.items():
            with self.subTest(sujeto=sujeto), self.como_app(usuario_id=sujeto):
                self.assertEqual(DispositivoCampo.objects.count(), esperado)
        with self.como_app(usuario_id=self.tc.pk, fase="preingreso"):
            self.assertEqual(DispositivoCampo.objects.count(), 0)

    def test_solo_ac_registra_y_revoca(self):
        d = self.registrar(self.tc)
        insertar = (
            "INSERT INTO cuentas_dispositivocampo (responsable_id, etiqueta, algoritmo, llave_publica, huella,"
            " estado, registrado_por_id, registrado_en, motivo_revocacion) VALUES (%s, 'x', 'ED25519', 'x',"
            " md5(random()::text), 'ACTIVO', %s, now(), '')"
        )
        for sujeto in (self.tc, self.gc):
            with self.subTest(sujeto=sujeto.username), self.como_app(usuario_id=sujeto.pk):
                self.assertIsInstance(self.ajeno(insertar, [self.tc.pk, sujeto.pk]), DatabaseError)
                filas = self.ajeno("UPDATE cuentas_dispositivocampo SET estado = 'REVOCADO', revocado_en = now(),"
                                   " revocado_por_id = %s WHERE id = %s", [sujeto.pk, d.pk])
                self.assertEqual(filas, 0)

    def test_ac_no_cambia_la_llave(self):
        d = self.registrar(self.tc)
        with self.como_app(usuario_id=self.ac.pk):
            cambio = self.ajeno("UPDATE cuentas_dispositivocampo SET responsable_id = %s WHERE id = %s",
                                [self.tc2.pk, d.pk])
            self.assertIsInstance(cambio, DatabaseError)
