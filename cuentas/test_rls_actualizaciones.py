"""
Endurecimiento de las actualizaciones bajo RLS (migración 0019).

Con varias políticas de UPDATE, la fila vieja puede entrar por el USING de una y
la nueva salir por el WITH CHECK de otra. Estas pruebas intentan esas
combinaciones con SQL directo, como lo haría alguien que ya ejecuta consultas
con el rol de la aplicación.
"""

from django.db import DatabaseError, connection
from django.utils import timezone

from cuentas import rls, services, sesion
from cuentas.models import EstadoCuenta, RolUsuario, Sesion
from cuentas.test_gestion import PERMISIVO
from cuentas.test_rls import RLSBase
from cuentas.test_roles_c7 import con_rol


@PERMISIVO
class ActualizacionesRLSTests(RLSBase):
    def setUp(self):
        super().setUp()
        self.ac = con_rol("admin_ac", "AC")
        self.g1 = con_rol("gerente1", "GC")
        self.g2 = con_rol("gerente2", "GC")
        self.tc = con_rol("tecnica", "TC")

    def falla(self, sql, params):
        self.assertIsInstance(self.ajeno(sql, params), DatabaseError)

    def test_nadie_reactiva_ni_reabre_cuentas(self):
        inactiva = con_rol("inactiva", "PF")
        services.dar_de_baja(self.ac, inactiva)
        casos = [(inactiva.pk, "ACTIVA", "true"), (inactiva.pk, "PENDIENTE", "false"),
                 (self.ana.pk, "PENDIENTE", "false")]
        for sujeto in (self.ac, self.g1):
            for cuenta, estado, activa in casos:
                with self.subTest(sujeto=sujeto.username, cuenta=cuenta, estado=estado), \
                        self.como_app(usuario_id=sujeto.pk):
                    resultado = self.ajeno(
                        f"UPDATE cuentas_usuario SET estado = '{estado}', is_active = {activa} WHERE id = %s", [cuenta])
                    self.assertTrue(resultado == 0 or isinstance(resultado, DatabaseError), resultado)
        inactiva.refresh_from_db()
        self.assertEqual(inactiva.estado, EstadoCuenta.INACTIVA)

    def test_las_transiciones_validas_siguen_funcionando(self):
        with self.como_app(usuario_id=self.ac.pk):
            cuenta = services.crear_cuenta(self.ac, username="nueva", rol="PF")
            services.dar_de_baja(self.ac, cuenta)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, EstadoCuenta.INACTIVA)

    def test_ac_no_convierte_un_rol_en_otro(self):
        fila = RolUsuario.objects.get(usuario=self.tc, rol="TC")
        with self.como_app(usuario_id=self.ac.pk):
            for cambio in ("rol = 'GC'", "rol = 'VF'", f"usuario_id = {self.ana.pk}", "vigente_desde = now() - interval '1 year'"):
                with self.subTest(cambio=cambio):
                    self.falla(f"UPDATE cuentas_rolusuario SET {cambio} WHERE id = %s", [fila.pk])
            self.assertEqual(self.ajeno("UPDATE cuentas_rolusuario SET vigente_hasta = statement_timestamp() WHERE id = %s",
                                        [fila.pk]), 1)  # cerrarla sí

    def test_una_vigencia_cerrada_no_se_reabre_ni_se_alarga(self):
        with self.como_app(usuario_id=self.ac.pk):
            solicitud = services.retirar_rol(self.ac, self.g2, "GC")
        with self.como_app(usuario_id=self.g1.pk):
            services.autorizar_solicitud(self.g1, solicitud)
            fila = RolUsuario.objects.get(usuario=self.g2, rol="GC")
            self.falla("UPDATE cuentas_rolusuario SET vigente_hasta = NULL WHERE id = %s", [fila.pk])
            self.falla("UPDATE cuentas_rolusuario SET vigente_hasta = vigente_hasta + interval '1 day'"
                       " WHERE id = %s", [fila.pk])

    def test_nadie_reabre_su_sesion_ni_mueve_su_inicio(self):
        abierta = sesion.registrar_inicio(self.ana, "clave-1")
        cerrada = sesion.registrar_inicio(self.ana, "clave-2")
        sesion.cerrar_registro("clave-2", "FORZADO")
        with self.como_app(usuario_id=self.ana.pk):
            self.falla("UPDATE cuentas_sesion SET cerrada_en = NULL, motivo_cierre = '' WHERE id = %s", [cerrada.pk])
            self.falla("UPDATE cuentas_sesion SET motivo_cierre = 'SALIDA' WHERE id = %s", [cerrada.pk])
            self.falla("UPDATE cuentas_sesion SET iniciada_en = now() WHERE id = %s", [abierta.pk])
            self.falla(f"UPDATE cuentas_sesion SET usuario_id = {self.beto.pk} WHERE id = %s", [abierta.pk])
            self.assertEqual(self.ajeno("UPDATE cuentas_sesion SET ultima_actividad = now() WHERE id = %s",
                                        [abierta.pk]), 1)

    def test_ac_y_gc_cierran_pero_no_reabren_sesiones_ajenas(self):
        r = sesion.registrar_inicio(self.ana, "clave-3")
        sesion.cerrar_registro("clave-3", "FORZADO")
        for sujeto in (self.ac, self.g1):
            with self.subTest(sujeto=sujeto.username), self.como_app(usuario_id=sujeto.pk):
                resultado = self.ajeno("UPDATE cuentas_sesion SET cerrada_en = NULL WHERE id = %s", [r.pk])
                self.assertTrue(resultado == 0 or isinstance(resultado, DatabaseError), resultado)
        self.assertIsNotNone(Sesion.objects.get(pk=r.pk).cerrada_en)

    def test_sistema_si_puede(self):
        r = sesion.registrar_inicio(self.ana, "clave-4")
        rls.fijar_contexto(fase="sistema")
        try:
            with connection.cursor() as c:
                c.execute("UPDATE cuentas_sesion SET iniciada_en = %s WHERE id = %s", [timezone.now(), r.pk])
                self.assertEqual(c.rowcount, 1)
        finally:
            rls.fijar_contexto()
