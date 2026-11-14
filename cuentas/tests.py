from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.test import TestCase

from cuentas import services
from cuentas.models import EstadoCuenta, Usuario
from nucleo.errores import FueraDeAlcance, TransicionInvalida

CLAVE = "clave-de-prueba-larga-123"


def crear_cuenta(username="ana", estado=None):
    cuenta = Usuario.objects.create_user(username=username, password=CLAVE)
    if estado in (EstadoCuenta.ACTIVA, EstadoCuenta.EN_BAJA, EstadoCuenta.INACTIVA):
        services.cambiar_estado(cuenta, EstadoCuenta.ACTIVA)
    if estado in (EstadoCuenta.EN_BAJA,):
        services.cambiar_estado(cuenta, EstadoCuenta.EN_BAJA)
    if estado == EstadoCuenta.INACTIVA:
        services.cambiar_estado(cuenta, EstadoCuenta.INACTIVA)
    cuenta.refresh_from_db()
    return cuenta


class EstadoInicialTests(TestCase):
    def test_cuenta_nueva_nace_pendiente_e_inactiva(self):
        cuenta = crear_cuenta()
        self.assertEqual(cuenta.estado, EstadoCuenta.PENDIENTE)
        self.assertFalse(cuenta.is_active)

    def test_superusuario_nace_activo(self):
        admin = Usuario.objects.create_superuser("admin", password=CLAVE)
        self.assertEqual(admin.estado, EstadoCuenta.ACTIVA)
        self.assertTrue(admin.is_active)


class TransicionesTests(TestCase):
    VALIDAS = [
        (EstadoCuenta.PENDIENTE, EstadoCuenta.ACTIVA),
        (EstadoCuenta.PENDIENTE, EstadoCuenta.INACTIVA),
        (EstadoCuenta.ACTIVA, EstadoCuenta.EN_BAJA),
        (EstadoCuenta.ACTIVA, EstadoCuenta.INACTIVA),
        (EstadoCuenta.EN_BAJA, EstadoCuenta.INACTIVA),
    ]

    def test_transiciones_validas(self):
        for i, (desde, hacia) in enumerate(self.VALIDAS):
            with self.subTest(desde=desde, hacia=hacia):
                cuenta = crear_cuenta(f"u{i}", desde)
                cuenta, anterior = services.cambiar_estado(cuenta, hacia)
                cuenta.refresh_from_db()
                self.assertEqual(anterior, desde)
                self.assertEqual(cuenta.estado, hacia)
                self.assertEqual(cuenta.is_active, hacia == EstadoCuenta.ACTIVA)
                self.assertIsNotNone(cuenta.estado_desde)

    def test_transiciones_invalidas(self):
        validas = set(self.VALIDAS)
        i = 0
        for desde in EstadoCuenta.values:
            for hacia in EstadoCuenta.values:
                if (desde, hacia) in validas:
                    continue
                i += 1
                with self.subTest(desde=desde, hacia=hacia):
                    cuenta = crear_cuenta(f"x{i}", desde)
                    with self.assertRaises(TransicionInvalida):
                        services.cambiar_estado(cuenta, hacia)
                    cuenta.refresh_from_db()
                    self.assertEqual(cuenta.estado, desde)

    def test_inactiva_es_final(self):
        cuenta = crear_cuenta(estado=EstadoCuenta.INACTIVA)
        self.assertEqual(services.TRANSICIONES[EstadoCuenta.INACTIVA], frozenset())
        with self.assertRaises(TransicionInvalida):
            services.cambiar_estado(cuenta, EstadoCuenta.ACTIVA)

    def test_estado_desconocido(self):
        cuenta = crear_cuenta()
        with self.assertRaises(TransicionInvalida):
            services.cambiar_estado(cuenta, "SUPERADMIN")

    def test_cuenta_inexistente_es_fuera_de_alcance(self):
        with self.assertRaises(FueraDeAlcance):
            services.cambiar_estado(999_999, EstadoCuenta.ACTIVA)

    def test_acepta_pk(self):
        cuenta = crear_cuenta()
        services.cambiar_estado(cuenta.pk, EstadoCuenta.ACTIVA)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.ACTIVA)

    def test_relee_de_la_base_y_no_confia_en_el_objeto(self):
        cuenta = crear_cuenta(estado=EstadoCuenta.ACTIVA)
        copia_vieja = Usuario.objects.get(pk=cuenta.pk)
        services.cambiar_estado(cuenta, EstadoCuenta.INACTIVA)
        # La copia en memoria dice ACTIVA, pero la base dice INACTIVA (final).
        with self.assertRaises(TransicionInvalida):
            services.cambiar_estado(copia_vieja, EstadoCuenta.EN_BAJA)


class AutenticacionSegunEstadoTests(TestCase):
    def test_solo_la_cuenta_activa_se_autentica(self):
        for estado in EstadoCuenta.values:
            with self.subTest(estado=estado):
                cuenta = crear_cuenta(f"a-{estado.lower()}", estado)
                resultado = authenticate(username=cuenta.username, password=CLAVE)
                if estado == EstadoCuenta.ACTIVA:
                    self.assertEqual(resultado, cuenta)
                else:
                    self.assertIsNone(resultado)


class RestriccionBaseDeDatosTests(TestCase):
    def test_la_base_rechaza_activa_sin_is_active(self):
        cuenta = crear_cuenta(estado=EstadoCuenta.ACTIVA)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Usuario.objects.filter(pk=cuenta.pk).update(is_active=False)

    def test_la_base_rechaza_is_active_sin_estado_activa(self):
        cuenta = crear_cuenta(estado=EstadoCuenta.EN_BAJA)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Usuario.objects.filter(pk=cuenta.pk).update(is_active=True)

    def test_save_deriva_is_active_del_estado(self):
        cuenta = crear_cuenta()
        cuenta.is_active = True  # intento de saltarse el estado
        cuenta.save()
        cuenta.refresh_from_db()
        self.assertFalse(cuenta.is_active)
