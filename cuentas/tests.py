from datetime import timedelta

from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from autorizacion import roles
from cuentas import services
from cuentas.models import EstadoCuenta, Rol, RolUsuario, Usuario
from cuentas.selectores import roles_vigentes, tiene_rol
from nucleo.errores import (
    DatoInvalido,
    FueraDeAlcance,
    SeparacionDeFunciones,
    TransicionInvalida,
)

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


# ===========================================================================
# Paso 2 · Roles con vigencia y pares excluyentes
# ===========================================================================
class RolesBaseTests(TestCase):
    def setUp(self):
        self.ahora = timezone.now()
        self.u = crear_cuenta("rosa", EstadoCuenta.ACTIVA)

    def hace(self, **kw):
        return self.ahora - timedelta(**kw)

    def en(self, **kw):
        return self.ahora + timedelta(**kw)


class RolChoicesTests(TestCase):
    def test_choices_coinciden_con_el_modulo_puro(self):
        self.assertEqual(set(Rol.values), set(roles.ROLES))


class RolesVigentesTests(RolesBaseTests):
    def test_sin_roles(self):
        self.assertEqual(roles_vigentes(self.u), set())

    def test_rol_abierto_es_vigente(self):
        services.agregar_rol(self.u, "TC")
        self.assertEqual(roles_vigentes(self.u), {"TC"})
        self.assertTrue(tiene_rol(self.u.pk, "TC"))

    def test_rol_futuro_no_es_vigente_hoy(self):
        services.agregar_rol(self.u, "GC", desde=self.en(days=1))
        self.assertEqual(roles_vigentes(self.u), set())
        self.assertEqual(roles_vigentes(self.u, en=self.en(days=2)), {"GC"})

    def test_rol_vencido_no_es_vigente(self):
        services.agregar_rol(self.u, "PF", desde=self.hace(days=10), hasta=self.hace(days=1))
        self.assertEqual(roles_vigentes(self.u), set())
        self.assertEqual(roles_vigentes(self.u, en=self.hace(days=5)), {"PF"})

    def test_limite_hasta_es_exclusivo(self):
        fin = self.en(hours=1)
        services.agregar_rol(self.u, "PF", desde=self.hace(hours=1), hasta=fin)
        self.assertEqual(roles_vigentes(self.u, en=fin - timedelta(microseconds=1)), {"PF"})
        self.assertEqual(roles_vigentes(self.u, en=fin), set())

    def test_varios_roles_compatibles(self):
        services.agregar_rol(self.u, "TC")
        services.agregar_rol(self.u, "PF")
        self.assertEqual(roles_vigentes(self.u), {"TC", "PF"})

    def test_no_mezcla_usuarios(self):
        otro = crear_cuenta("otro", EstadoCuenta.ACTIVA)
        services.agregar_rol(otro, "GC")
        self.assertEqual(roles_vigentes(self.u), set())


class AgregarRolTests(RolesBaseTests):
    def test_rol_desconocido(self):
        with self.assertRaises(DatoInvalido):
            services.agregar_rol(self.u, "ROOT")

    def test_vigencia_invertida(self):
        with self.assertRaises(DatoInvalido):
            services.agregar_rol(self.u, "TC", desde=self.ahora, hasta=self.hace(days=1))

    def test_duplicado_se_niega(self):
        services.agregar_rol(self.u, "TC")
        with self.assertRaises(TransicionInvalida):
            services.agregar_rol(self.u, "TC")
        self.assertEqual(RolUsuario.objects.filter(usuario=self.u).count(), 1)

    def test_mismo_rol_en_tramos_que_no_se_cruzan(self):
        services.agregar_rol(self.u, "AE", desde=self.hace(days=40), hasta=self.hace(days=20))
        services.agregar_rol(self.u, "AE", desde=self.hace(days=10), hasta=self.en(days=10))
        self.assertEqual(roles_vigentes(self.u), {"AE"})

    def test_cada_par_excluyente_se_niega_en_ambos_sentidos(self):
        for i, par in enumerate(roles.EXCLUYENTES):
            a, b = sorted(par)
            for primero, segundo in ((a, b), (b, a)):
                with self.subTest(primero=primero, segundo=segundo):
                    u = crear_cuenta(f"e{i}{primero}", EstadoCuenta.ACTIVA)
                    services.agregar_rol(u, primero)
                    with self.assertRaises(SeparacionDeFunciones):
                        services.agregar_rol(u, segundo)
                    self.assertEqual(roles_vigentes(u), {primero})

    def test_pf_convive_con_cualquier_rol(self):
        for r in ("TC", "VF", "GC", "AC", "AE"):
            with self.subTest(rol=r):
                u = crear_cuenta(f"pf{r}", EstadoCuenta.ACTIVA)
                services.agregar_rol(u, "PF")
                services.agregar_rol(u, r)
                self.assertEqual(roles_vigentes(u), {"PF", r})

    def test_excluyente_futuro_tambien_se_niega(self):
        services.agregar_rol(self.u, "GC", desde=self.en(days=5))
        with self.assertRaises(SeparacionDeFunciones):
            services.agregar_rol(self.u, "AC")

    def test_excluyente_que_se_cruza_parcialmente_se_niega(self):
        services.agregar_rol(self.u, "TC", desde=self.hace(days=10), hasta=self.en(days=2))
        with self.assertRaises(SeparacionDeFunciones):
            services.agregar_rol(self.u, "VF", desde=self.en(days=1))

    def test_excluyente_terminado_ya_no_choca(self):
        # V5: quien fue TC puede ser VF después; C2 se encarga por objeto.
        services.agregar_rol(self.u, "TC", desde=self.hace(days=30))
        services.terminar_rol(self.u, "TC")
        services.agregar_rol(self.u, "VF")
        self.assertEqual(roles_vigentes(self.u), {"VF"})

    def test_excluyente_que_empieza_al_terminar_el_otro(self):
        fin = self.en(days=3)
        services.agregar_rol(self.u, "TC", hasta=fin)
        services.agregar_rol(self.u, "VF", desde=fin)
        self.assertEqual(roles_vigentes(self.u), {"TC"})
        self.assertEqual(roles_vigentes(self.u, en=fin), {"VF"})

    def test_cuenta_inexistente(self):
        with self.assertRaises(FueraDeAlcance):
            services.agregar_rol(999_999, "TC")


class TerminarRolTests(RolesBaseTests):
    def test_termina_y_conserva_historial(self):
        services.agregar_rol(self.u, "TC", desde=self.hace(days=1))
        p = services.terminar_rol(self.u, "TC")
        self.assertIsNotNone(p.vigente_hasta)
        self.assertEqual(roles_vigentes(self.u), set())
        self.assertEqual(RolUsuario.objects.filter(usuario=self.u, rol="TC").count(), 1)

    def test_sin_rol_vigente(self):
        with self.assertRaises(TransicionInvalida):
            services.terminar_rol(self.u, "TC")

    def test_rol_futuro_no_se_termina_como_vigente(self):
        services.agregar_rol(self.u, "GC", desde=self.en(days=1))
        with self.assertRaises(TransicionInvalida):
            services.terminar_rol(self.u, "GC")

    def test_rol_desconocido(self):
        with self.assertRaises(DatoInvalido):
            services.terminar_rol(self.u, "ROOT")


class RolesBaseDeDatosTests(RolesBaseTests):
    def test_la_base_rechaza_dos_abiertos_del_mismo_rol(self):
        RolUsuario.objects.create(usuario=self.u, rol="TC", vigente_desde=self.ahora)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RolUsuario.objects.create(usuario=self.u, rol="TC", vigente_desde=self.en(days=1))

    def test_la_base_rechaza_vigencia_invertida(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            RolUsuario.objects.create(
                usuario=self.u, rol="TC", vigente_desde=self.ahora, vigente_hasta=self.hace(days=1)
            )

    def test_la_base_rechaza_rol_desconocido(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            RolUsuario.objects.create(usuario=self.u, rol="XX", vigente_desde=self.ahora)


# ===========================================================================
# Paso 3 · usuarios_con_rol() conectado al gancho
# ===========================================================================
class UsuariosConRolTests(RolesBaseTests):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group

        from nucleo import ganchos

        self.ganchos = ganchos
        self.Group = Group

    def ids(self, rol):
        return set(self.ganchos.usuarios_con_rol(rol).values_list("pk", flat=True))

    def test_el_gancho_apunta_a_la_implementacion_real(self):
        from django.conf import settings

        self.assertEqual(settings.VERDEAZUL_GANCHOS["usuarios_con_rol"], "cuentas.roles.usuarios_con_rol")

    def test_devuelve_activos_con_rol_vigente(self):
        services.agregar_rol(self.u, "VF")
        self.assertEqual(self.ids("VF"), {self.u.pk})
        self.assertEqual(self.ids("TC"), set())

    def test_excluye_cuentas_que_no_estan_activas(self):
        for estado in (EstadoCuenta.PENDIENTE, EstadoCuenta.EN_BAJA, EstadoCuenta.INACTIVA):
            with self.subTest(estado=estado):
                u = crear_cuenta(f"vf-{estado.lower()}", EstadoCuenta.ACTIVA if estado != EstadoCuenta.PENDIENTE else None)
                services.agregar_rol(u, "VF")
                if estado != EstadoCuenta.PENDIENTE:
                    services.cambiar_estado(u, estado)
                self.assertNotIn(u.pk, self.ids("VF"))

    def test_excluye_roles_futuros_y_vencidos(self):
        futuro = crear_cuenta("futuro", EstadoCuenta.ACTIVA)
        vencido = crear_cuenta("vencido", EstadoCuenta.ACTIVA)
        services.agregar_rol(futuro, "GC", desde=self.en(days=1))
        services.agregar_rol(vencido, "GC", desde=self.hace(days=9), hasta=self.hace(days=1))
        self.assertEqual(self.ids("GC"), set())

    def test_ignora_los_groups_de_django(self):
        grupo = self.Group.objects.create(name="VF")
        self.u.groups.add(grupo)
        self.assertEqual(self.ids("VF"), set())

    def test_rol_desconocido_devuelve_vacio(self):
        services.agregar_rol(self.u, "TC")
        for rol in ("XX", "tc", "", None):
            with self.subTest(rol=rol):
                self.assertFalse(self.ganchos.usuarios_con_rol(rol).exists())

    def test_es_un_queryset_encadenable_sin_duplicados(self):
        otro = crear_cuenta("otro-vf", EstadoCuenta.ACTIVA)
        services.agregar_rol(self.u, "VF", desde=self.hace(days=20), hasta=self.hace(days=10))
        services.agregar_rol(self.u, "VF", desde=self.hace(days=5))
        services.agregar_rol(otro, "VF")
        qs = self.ganchos.usuarios_con_rol("VF")
        self.assertEqual(qs.count(), 2)
        self.assertEqual(list(qs.exclude(pk__in={otro.pk}).values_list("pk", flat=True)), [self.u.pk])
        self.assertTrue(qs.filter(pk=otro.pk).exists())

    def test_rol_terminado_sale_de_la_lista(self):
        services.agregar_rol(self.u, "TC", desde=self.hace(days=1))
        services.terminar_rol(self.u, "TC")
        self.assertEqual(self.ids("TC"), set())


class IntegracionCatalogoTests(TestCase):
    """crear_finca exige que el propietario tenga rol PF vía usuarios_con_rol."""

    def setUp(self):
        self.ac = crear_cuenta("ac", EstadoCuenta.ACTIVA)
        services.agregar_rol(self.ac, "AC")
        self.dueno = crear_cuenta("dueno", EstadoCuenta.ACTIVA)

    def crear(self):
        from django.test import override_settings

        from catalogo import services as catalogo

        # El motor aún no existe (paso 13): se activa explícitamente el modo permisivo.
        with override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=True):
            return catalogo.crear_finca(self.ac, "La Esperanza", "San Carlos", self.dueno)

    def test_propietario_sin_rol_pf_se_niega(self):
        with self.assertRaises(DatoInvalido):
            self.crear()

    def test_propietario_con_rol_pf_se_acepta(self):
        services.agregar_rol(self.dueno, "PF")
        self.assertEqual(self.crear().propietario, self.dueno)

    def test_propietario_pf_con_cuenta_inactiva_se_niega(self):
        services.agregar_rol(self.dueno, "PF")
        services.cambiar_estado(self.dueno, EstadoCuenta.INACTIVA)
        with self.assertRaises(DatoInvalido):
            self.crear()
