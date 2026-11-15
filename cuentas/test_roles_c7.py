"""
Paso 8 · C7: AC asigna y retira roles (AC·O7b AR, RR); GC autoriza o rechaza
los cambios de GC y AE (GC·O7b AU, RE). Incluye V10.
"""

from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from cuentas import services, sesion
from cuentas.models import (
    EstadoCuenta,
    EstadoSolicitudRol,
    MotivoCierre,
    Sesion,
    SolicitudRol,
    TipoSolicitudRol,
)
from cuentas.selectores import roles_vigentes
from cuentas.test_gestion import EVENTOS, PERMISIVO, ultimo
from cuentas.test_rls import RLSBase
from cuentas.tests import crear_cuenta
from nucleo.errores import (
    AccesoDenegado,
    DatoInvalido,
    FueraDeAlcance,
    SeparacionDeFunciones,
    TransicionInvalida,
)


def con_rol(username, *roles_usuario, estado=EstadoCuenta.ACTIVA):
    u = crear_cuenta(username, estado)
    for r in roles_usuario:
        services.agregar_rol(u, r)
    return u


def abiertas(usuario) -> int:
    return Sesion.objects.filter(usuario=usuario, cerrada_en__isnull=True).count()


@PERMISIVO
class C7Base(TestCase):
    def setUp(self):
        EVENTOS.clear()
        self.ac = con_rol("admin_ac", "AC")
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")
        self.u = con_rol("rosa", "PF")

    def abrir_sesiones(self, usuario, n=2):
        for i in range(n):
            sesion.registrar_inicio(usuario, f"{usuario.pk}-{i}")


class AsignarTests(C7Base):
    def test_rol_comun_tiene_efecto_y_cierra_sesiones(self):
        self.abrir_sesiones(self.u)
        services.asignar_rol(self.ac, self.u, "TC")
        self.assertEqual(roles_vigentes(self.u), {"PF", "TC"})
        self.assertEqual(abiertas(self.u), 0)
        self.assertEqual(
            set(Sesion.objects.filter(usuario=self.u).values_list("motivo_cierre", flat=True)),
            {MotivoCierre.FORZADO},
        )
        self.assertEqual(ultimo("rol:asignar")["despues"], {"rol": "TC", "sesiones_cerradas": 2})

    def test_v10_rol_excluyente_se_niega(self):
        tecnico = con_rol("tecnico", "TC")
        with self.assertRaises(SeparacionDeFunciones):
            services.asignar_rol(self.ac, tecnico, "VF")
        self.assertEqual(roles_vigentes(tecnico), {"TC"})
        self.assertEqual(ultimo("rol:asignar")["resultado"], "DENEGADO")

    def test_v10_nadie_se_asigna_roles_a_si_mismo(self):
        for rol in ("GC", "TC", "PF"):
            with self.subTest(rol=rol), self.assertRaises(SeparacionDeFunciones):
                services.asignar_rol(self.ac, self.ac, rol)
        self.assertEqual(roles_vigentes(self.ac), {"AC"})
        self.assertFalse(SolicitudRol.objects.exists())

    def test_ae_es_excluyente_con_tc_vf_gc_y_ac(self):
        for rol in ("TC", "VF", "GC"):
            with self.subTest(rol=rol), self.assertRaises(SeparacionDeFunciones):
                services.asignar_rol(self.ac, con_rol(f"con-{rol}", rol), "AE")

    def test_solo_cuentas_activas(self):
        for estado in (EstadoCuenta.PENDIENTE, EstadoCuenta.EN_BAJA, EstadoCuenta.INACTIVA):
            cuenta = crear_cuenta(f"c-{estado.lower()}")
            if estado != EstadoCuenta.PENDIENTE:
                services.cambiar_estado(cuenta, EstadoCuenta.ACTIVA)
                services.cambiar_estado(cuenta, estado)
            with self.subTest(estado=estado), self.assertRaises(TransicionInvalida):
                services.asignar_rol(self.ac, cuenta, "TC")

    def test_rol_repetido_o_desconocido(self):
        with self.assertRaises(TransicionInvalida):
            services.asignar_rol(self.ac, self.u, "PF")
        with self.assertRaises(DatoInvalido):
            services.asignar_rol(self.ac, self.u, "XX")

    def test_gc_y_ae_quedan_como_solicitud_sin_efecto(self):
        self.abrir_sesiones(self.u)
        solicitud = services.asignar_rol(self.ac, self.u, "GC")
        self.assertIsInstance(solicitud, SolicitudRol)
        self.assertEqual(
            (solicitud.tipo, solicitud.estado, solicitud.solicitada_por),
            (TipoSolicitudRol.ASIGNAR, EstadoSolicitudRol.SOLICITADA, self.ac),
        )
        self.assertEqual(roles_vigentes(self.u), {"PF"})
        self.assertEqual(abiertas(self.u), 2)  # sin efecto, sin cierre
        ae = services.asignar_rol(self.ac, self.u, "AE")
        self.assertEqual(ae.rol, "AE")

    def test_una_sola_solicitud_en_curso_por_rol(self):
        services.asignar_rol(self.ac, self.u, "GC")
        with self.assertRaises(TransicionInvalida):
            services.asignar_rol(self.ac, self.u, "GC")

    @override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=False)
    def test_sin_motor_se_niega(self):
        with self.assertRaises(AccesoDenegado):
            services.asignar_rol(self.ac, self.u, "TC")
        self.assertEqual(roles_vigentes(self.u), {"PF"})


class RetirarTests(C7Base):
    def test_rol_comun_se_retira_y_cierra_sesiones(self):
        services.asignar_rol(self.ac, self.u, "TC")
        self.abrir_sesiones(self.u)
        services.retirar_rol(self.ac, self.u, "TC")
        self.assertEqual(roles_vigentes(self.u), {"PF"})
        self.assertEqual(abiertas(self.u), 0)
        self.assertEqual(ultimo("rol:retirar")["despues"]["sesiones_cerradas"], 2)

    def test_rol_que_no_tiene_o_propio(self):
        with self.assertRaises(TransicionInvalida):
            services.retirar_rol(self.ac, self.u, "TC")
        with self.assertRaises(SeparacionDeFunciones):
            services.retirar_rol(self.ac, self.ac, "AC")

    def test_retirar_gc_queda_como_solicitud(self):
        self.abrir_sesiones(self.g1)
        solicitud = services.retirar_rol(self.ac, self.g1, "GC")
        self.assertEqual(solicitud.tipo, TipoSolicitudRol.RETIRAR)
        self.assertEqual(roles_vigentes(self.g1), {"GC"})
        self.assertEqual(abiertas(self.g1), 2)


class AutorizarTests(C7Base):
    def test_autorizar_asignacion(self):
        solicitud = services.asignar_rol(self.ac, self.u, "GC")
        self.abrir_sesiones(self.u)
        services.autorizar_solicitud(self.g1, solicitud)
        solicitud.refresh_from_db()
        self.assertEqual((solicitud.estado, solicitud.resuelta_por), (EstadoSolicitudRol.AUTORIZADA, self.g1))
        self.assertIsNotNone(solicitud.resuelta_en)
        self.assertEqual(roles_vigentes(self.u), {"PF", "GC"})
        self.assertEqual(abiertas(self.u), 0)
        self.assertEqual(ultimo("rol:autorizar")["despues"]["sesiones_cerradas"], 2)

    def test_autorizar_retiro(self):
        solicitud = services.retirar_rol(self.ac, self.g2, "GC")
        self.abrir_sesiones(self.g2)
        services.autorizar_solicitud(self.g1, solicitud)
        self.assertEqual(roles_vigentes(self.g2), set())
        self.assertEqual(abiertas(self.g2), 0)

    def test_no_autoriza_quien_la_pidio(self):
        # Con el motor permisivo, un GC puede pedirla; aun así no la resuelve él.
        solicitud = services.asignar_rol(self.g1, self.u, "AE")
        with self.assertRaises(SeparacionDeFunciones):
            services.autorizar_solicitud(self.g1, solicitud)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, EstadoSolicitudRol.SOLICITADA)

    def test_no_autoriza_la_persona_destino(self):
        solicitud = services.retirar_rol(self.ac, self.g2, "GC")
        for accion in (services.autorizar_solicitud, services.rechazar_solicitud):
            with self.subTest(accion=accion.__name__), self.assertRaises(SeparacionDeFunciones):
                accion(self.g2, solicitud)

    def test_e_se_reevalua_al_autorizar(self):
        solicitud = services.asignar_rol(self.ac, self.u, "GC")
        services.asignar_rol(self.ac, self.u, "AC")  # AC ⊕ GC, recibido mientras tanto
        with self.assertRaises(SeparacionDeFunciones):
            services.autorizar_solicitud(self.g1, solicitud)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, EstadoSolicitudRol.SOLICITADA)
        self.assertNotIn("GC", roles_vigentes(self.u))

    def test_una_solicitud_se_resuelve_una_vez(self):
        solicitud = services.asignar_rol(self.ac, self.u, "GC")
        services.autorizar_solicitud(self.g1, solicitud)
        for accion in (services.autorizar_solicitud, services.rechazar_solicitud):
            with self.subTest(accion=accion.__name__), self.assertRaises(TransicionInvalida):
                accion(self.g2, solicitud)

    def test_rechazar(self):
        solicitud = services.asignar_rol(self.ac, self.u, "GC")
        services.rechazar_solicitud(self.g1, solicitud, motivo=" Sin justificación ")
        solicitud.refresh_from_db()
        self.assertEqual((solicitud.estado, solicitud.motivo), (EstadoSolicitudRol.RECHAZADA, "Sin justificación"))
        self.assertEqual(roles_vigentes(self.u), {"PF"})
        # Rechazada, se puede volver a pedir.
        services.asignar_rol(self.ac, self.u, "GC")

    def test_cuenta_dada_de_baja_mientras_tanto(self):
        solicitud = services.asignar_rol(self.ac, self.u, "GC")
        services.dar_de_baja(self.ac, self.u)
        with self.assertRaises(TransicionInvalida):
            services.autorizar_solicitud(self.g1, solicitud)

    def test_solicitud_inexistente(self):
        with self.assertRaises(FueraDeAlcance):
            services.autorizar_solicitud(self.g1, 999999)


class CuentaNuevaConRolGCTests(C7Base):
    def test_cuenta_gc_queda_pendiente_con_solicitud_y_se_activa_al_autorizar(self):
        cuenta = services.crear_cuenta(self.ac, username="nuevo_gc", rol="GC")
        self.assertEqual(cuenta.estado, EstadoCuenta.PENDIENTE)
        solicitud = SolicitudRol.objects.get(usuario=cuenta)
        self.assertEqual((solicitud.rol, solicitud.solicitada_por), ("GC", self.ac))
        services.autorizar_solicitud(self.g1, solicitud)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.ACTIVA)
        self.assertEqual(roles_vigentes(cuenta), {"GC"})

    def test_rechazada_sigue_pendiente(self):
        cuenta = services.crear_cuenta(self.ac, username="nuevo_gc", rol="GC")
        services.rechazar_solicitud(self.g1, SolicitudRol.objects.get(usuario=cuenta))
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.PENDIENTE)

    def test_cuenta_ae_espera_su_ventana(self):
        hoy = timezone.localdate()
        cuenta = services.crear_cuenta(self.ac, username="auditora", rol="AE", inicio=hoy, fin=hoy)
        self.assertEqual(cuenta.estado, EstadoCuenta.PENDIENTE)
        self.assertFalse(SolicitudRol.objects.filter(usuario=cuenta).exists())


class RestriccionesDeLaBaseTests(C7Base):
    def crear(self, **datos):
        base = {"usuario": self.u, "rol": "GC", "tipo": TipoSolicitudRol.ASIGNAR, "solicitada_por": self.ac}
        with transaction.atomic():
            return SolicitudRol.objects.create(**{**base, **datos})

    def test_solo_gc_y_ae(self):
        with self.assertRaises(IntegrityError):
            self.crear(rol="TC")

    def test_resuelve_otra_persona(self):
        for quien in (self.ac, self.u):
            with self.subTest(quien=quien.username), self.assertRaises(IntegrityError):
                self.crear(estado=EstadoSolicitudRol.AUTORIZADA, resuelta_por=quien, resuelta_en=timezone.now())

    def test_resolucion_coherente(self):
        with self.assertRaises(IntegrityError):
            self.crear(estado=EstadoSolicitudRol.AUTORIZADA)


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------
@PERMISIVO
class C7RLSTests(RLSBase):
    def setUp(self):
        super().setUp()
        EVENTOS.clear()
        self.ac = con_rol("admin_ac", "AC")
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")

    def falla(self, funcion, *args, **kwargs):
        with self.assertRaises(DatabaseError), transaction.atomic():
            funcion(*args, **kwargs)

    def insertar_rol(self, usuario_id, rol):
        return self.ajeno(
            "INSERT INTO cuentas_rolusuario (usuario_id, rol, vigente_desde, creado_en) VALUES (%s, %s, now(), now())",
            [usuario_id, rol],
        )

    def test_ac_asigna_y_retira_roles_comunes(self):
        sesion.registrar_inicio(self.ana, "clave-ana")
        with self.como_app(usuario_id=self.ac.pk):
            services.asignar_rol(self.ac, self.ana, "TC")
            self.assertEqual(roles_vigentes(self.ana), {"PF", "TC"})
            services.retirar_rol(self.ac, self.ana, "TC")
        self.assertEqual(roles_vigentes(self.ana), {"PF"})
        self.assertEqual(abiertas(self.ana), 0)

    def test_ac_no_da_ni_quita_gc_o_ae_directamente(self):
        with self.como_app(usuario_id=self.ac.pk):
            for rol in ("GC", "AE"):
                with self.subTest(rol=rol):
                    self.assertIsInstance(self.insertar_rol(self.ana.pk, rol), DatabaseError)
            filas = self.ajeno("UPDATE cuentas_rolusuario SET vigente_hasta = now() WHERE usuario_id = %s",
                               [self.g1.pk])
            self.assertEqual(filas, 0)

    def test_flujo_completo_de_gc_con_rls(self):
        with self.como_app(usuario_id=self.ac.pk):
            cuenta = services.crear_cuenta(self.ac, username="nuevo_gc", rol="GC")
            solicitud = SolicitudRol.objects.get(usuario=cuenta)
        with self.como_app(usuario_id=self.g1.pk):
            services.autorizar_solicitud(self.g1, solicitud)
        cuenta.refresh_from_db()
        self.assertEqual((cuenta.estado, roles_vigentes(cuenta)), (EstadoCuenta.ACTIVA, {"GC"}))

    def test_retiro_de_gc_con_rls(self):
        sesion.registrar_inicio(self.g2, "clave-g2")
        with self.como_app(usuario_id=self.ac.pk):
            solicitud = services.retirar_rol(self.ac, self.g2, "GC")
        with self.como_app(usuario_id=self.g1.pk):
            services.autorizar_solicitud(self.g1, solicitud)
        self.assertEqual(roles_vigentes(self.g2), set())
        self.assertEqual(abiertas(self.g2), 0)

    def test_gc_no_da_roles_sin_solicitud_autorizada_por_el(self):
        with self.como_app(usuario_id=self.ac.pk):
            solicitud = services.asignar_rol(self.ac, self.ana, "GC")
        with self.como_app(usuario_id=self.g1.pk):
            # En curso (no autorizada) no basta, ni tampoco otros roles.
            self.assertIsInstance(self.insertar_rol(self.ana.pk, "GC"), DatabaseError)
            self.assertIsInstance(self.insertar_rol(self.ana.pk, "TC"), DatabaseError)
            services.autorizar_solicitud(self.g1, solicitud)
        with self.como_app(usuario_id=self.g2.pk):  # autorizada por otro GC
            self.assertIsInstance(self.insertar_rol(self.beto.pk, "AE"), DatabaseError)

    def test_gc_no_resuelve_solicitudes_sobre_si_mismo(self):
        with self.como_app(usuario_id=self.ac.pk):
            solicitud = services.retirar_rol(self.ac, self.g1, "GC")
        with self.como_app(usuario_id=self.g1.pk):
            filas = self.ajeno(
                "UPDATE cuentas_solicitudrol SET estado = 'RECHAZADA', resuelta_por_id = %s, resuelta_en = now()"
                " WHERE id = %s", [self.g1.pk, solicitud.pk])
            self.assertEqual(filas, 0)

    def test_solicitud_resuelta_no_cambia(self):
        with self.como_app(usuario_id=self.ac.pk):
            solicitud = services.asignar_rol(self.ac, self.ana, "GC")
        with self.como_app(usuario_id=self.g1.pk):
            cambio = self.ajeno("UPDATE cuentas_solicitudrol SET usuario_id = %s WHERE id = %s",
                                [self.beto.pk, solicitud.pk])
            self.assertIsInstance(cambio, DatabaseError)
            services.rechazar_solicitud(self.g1, solicitud)
        with self.como_app(usuario_id=self.g2.pk):
            filas = self.ajeno("UPDATE cuentas_solicitudrol SET estado = 'AUTORIZADA' WHERE id = %s", [solicitud.pk])
            self.assertEqual(filas, 0)

    def test_otros_roles_no_ven_ni_crean_solicitudes(self):
        with self.como_app(usuario_id=self.ac.pk):
            services.asignar_rol(self.ac, self.ana, "GC")
        with self.como_app(usuario_id=self.beto.pk):  # VF
            self.assertEqual(SolicitudRol.objects.count(), 0)
            self.falla(SolicitudRol.objects.create, usuario=self.ana, rol="AE",
                       tipo=TipoSolicitudRol.ASIGNAR, solicitada_por=self.beto)
        with self.como_app(usuario_id=self.g1.pk):
            self.assertEqual(SolicitudRol.objects.count(), 1)
