"""
Bitácora (O8): persistencia, encadenamiento y su verificación, manipulación
detectada, solo inserción (también para el dueño), negaciones que sobreviven al rollback,
concurrencia sin bifurcaciones, minimización, permisos (motor y RLS) y RG4.
"""

import threading
from datetime import date

from django.db import DatabaseError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from autorizacion import pep, reglas
from autorizacion.listas import listar
from autorizacion.reglas import CONCEDE, NIEGA
from autorizacion.test_cliente import con_rol
from autorizacion.test_listas import ListasBase
from bitacora import servicios
from bitacora.models import Evento
from cuentas import rls
from cuentas.test_rls import ROL
from mediciones import services as mediciones
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, TransicionInvalida

CON_EMISION_REAL = override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=True, VERDEAZUL_GANCHOS={
    "usuarios_con_rol": "cuentas.roles.usuarios_con_rol",
    "registrar_evento": "bitacora.servicios.registrar_evento",
    "on_medicion_aprobada": "certificados.servicios.emitir",
})


def registrar(actor=None, accion="periodo:abrir", resultado="OK", **extra):
    servicios.registrar_evento(actor=actor, accion=accion, recurso_tipo=extra.pop("recurso_tipo", "periodo"),
                               recurso_id=extra.pop("recurso_id", "1"), resultado=resultado, **extra)


class CadenaTests(TestCase):
    def setUp(self):
        self.gc = con_rol("gerente", "GC")

    def test_persiste_y_encadena(self):
        for i in range(5):
            registrar(self.gc, recurso_id=str(i), despues={"estado": "ABIERTO"})
        eventos = list(Evento.objects.order_by("secuencia"))
        self.assertEqual([e.secuencia for e in eventos], [1, 2, 3, 4, 5])
        self.assertEqual(eventos[0].hash_anterior, "0" * 64)
        for anterior, actual in zip(eventos, eventos[1:]):
            self.assertEqual(actual.hash_anterior, anterior.hash)
        self.assertEqual(len({e.hash for e in eventos}), 5)
        e = eventos[0]
        self.assertEqual((e.actor_id, e.actor_nombre, e.roles, e.resultado), (self.gc.pk, "gerente", ["GC"], "OK"))
        self.assertIsNotNone(e.momento)
        self.assertEqual(servicios.verificar_cadena(), [])

    def test_la_aplicacion_no_elige_su_lugar_en_la_cadena(self):
        registrar(self.gc)
        Evento.objects.create(secuencia=999, hash="f" * 64, hash_anterior="e" * 64, accion="x:y", resultado="OK")
        ultimo = Evento.objects.order_by("-secuencia").first()
        self.assertEqual(ultimo.secuencia, 2)  # el disparador ignora lo que mande la aplicación
        self.assertNotEqual(ultimo.hash, "f" * 64)
        self.assertEqual(servicios.verificar_cadena(), [])

    def test_solo_insercion_tambien_para_el_dueno(self):
        registrar(self.gc)
        for consulta in ("UPDATE bitacora_evento SET resultado = 'DENEGADO'", "DELETE FROM bitacora_evento"):
            with self.subTest(consulta=consulta), self.assertRaisesMessage(DatabaseError, "solo inserción"):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(consulta)
        self.assertEqual(Evento.objects.count(), 1)

    def test_manipular_con_privilegios_de_superusuario_se_detecta(self):
        for i in range(4):
            registrar(self.gc, recurso_id=str(i))
        with connection.cursor() as cursor:  # un superusuario puede saltarse los disparadores...
            cursor.execute("SET LOCAL session_replication_role = replica")
            cursor.execute("UPDATE bitacora_evento SET detalle = 'reescrito' WHERE secuencia = 2")
            cursor.execute("DELETE FROM bitacora_evento WHERE secuencia = 3")
            cursor.execute("SET LOCAL session_replication_role = origin")
        problemas = dict(servicios.verificar_cadena())  # ...pero la cadena lo delata
        self.assertIn("alterado", problemas[2])
        self.assertIn("hueco", problemas[3])
        self.assertEqual(set(problemas), {2, 3})  # tras un hueco se resincroniza: no repite el mismo daño

    def test_reemplazar_un_evento_entero_rompe_el_enlace_siguiente(self):
        for i in range(3):
            registrar(self.gc, recurso_id=str(i))
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role = replica")
            # Quien reescribe el evento 2 y recalcula su hash igual deja roto el enlace del 3.
            cursor.execute("UPDATE bitacora_evento SET detalle = 'reescrito' WHERE secuencia = 2")
            cursor.execute("UPDATE bitacora_evento e SET hash = bitacora_hash(e.hash_anterior, bitacora_canonico(e))"
                           " WHERE secuencia = 2")
            cursor.execute("SET LOCAL session_replication_role = origin")
        self.assertEqual(servicios.verificar_cadena(), [(3, "el enlace con el evento anterior no coincide")])

    def test_no_guarda_secretos_ni_datos_personales(self):
        registrar(self.gc, antes={"password": "p", "estado": "A"},
                  despues={"token": "t", "totp_secreto": "s", "codigos_recuperacion": ["1"], "llave_privada": "k",
                           "identificacion": "1-1111", "correo": "a@b.c", "anidado": {"clave": "x", "rol": "GC"},
                           "estado": "B", "codigo": "P1", "huella_clave": "ab12"})
        e = Evento.objects.get()
        texto = str(e.antes) + str(e.despues)
        for secreto in ("'p'", "'t'", "'s'", "'k'", "1-1111", "a@b.c", "'x'"):
            self.assertNotIn(secreto, texto)
        self.assertEqual((e.antes["estado"], e.despues["estado"], e.despues["anidado"]["rol"], e.despues["codigo"]),
                         ("A", "B", "GC", "P1"))  # lo no sensible sí queda
        self.assertEqual(e.despues["huella_clave"], "ab12")  # la huella de una clave pública no es secreta

    def test_lo_que_llega_largo_se_recorta(self):
        registrar(self.gc, detalle="x" * 5000, despues={"lista": list(range(500))})
        e = Evento.objects.get()
        self.assertEqual(len(e.detalle), servicios.MAX_DETALLE)
        self.assertEqual(len(e.despues["lista"]), servicios.MAX_ELEMENTOS)


@CON_EMISION_REAL
class ServiciosTests(TestCase):
    """Los servicios existentes ya llaman al gancho: ahora sus eventos quedan en la base."""

    def setUp(self):
        self.gc = con_rol("gerente", "GC")

    def test_un_cambio_deja_su_evento_y_si_se_revierte_no(self):
        periodo = mediciones.abrir_periodo(self.gc, "2026-A", date(2026, 1, 1), date(2026, 6, 30))
        e = Evento.objects.get(accion=Accion.PERIODO_ABRIR)
        self.assertEqual((e.recurso_id, e.resultado, e.despues), (str(periodo.pk), "OK", {"estado": "ABIERTO"}))
        with self.assertRaises(TransicionInvalida):  # cerrar un ABIERTO: se niega y no hay evento OK
            mediciones.cerrar_periodo(self.gc, periodo)
        self.assertFalse(Evento.objects.filter(accion=Accion.PERIODO_CERRAR, resultado="OK").exists())
        negado = Evento.objects.get(accion=Accion.PERIODO_CERRAR)
        self.assertEqual(negado.resultado, "DENEGADO")
        self.assertIn("TransicionInvalida", negado.detalle)

    def test_si_el_evento_no_se_puede_registrar_la_operacion_no_ocurre(self):
        from unittest import mock

        from mediciones.models import Periodo

        with mock.patch("bitacora.servicios._insertar", side_effect=DatabaseError("sin bitácora")), \
                self.assertRaises(DatabaseError):
            mediciones.abrir_periodo(self.gc, "2026-B", date(2026, 7, 1), date(2026, 12, 31))
        self.assertFalse(Periodo.objects.filter(nombre="2026-B").exists())  # RG4: fallo cerrado


@CON_EMISION_REAL
class NegacionesFueraDelRollbackTests(TransactionTestCase):
    """Sin la transacción envolvente de TestCase: lo que se revierte de verdad, se revierte."""

    def setUp(self):
        self.gc = con_rol("gerente", "GC")

    def test_la_negacion_de_un_servicio_sobrevive_al_rollback(self):
        periodo = mediciones.abrir_periodo(self.gc, "2026-A", date(2026, 1, 1), date(2026, 6, 30))
        with self.assertRaises(TransicionInvalida):
            mediciones.cerrar_periodo(self.gc, periodo)
        self.assertTrue(Evento.objects.filter(accion=Accion.PERIODO_CERRAR, resultado="DENEGADO").exists())
        self.assertEqual(servicios.verificar_cadena(), [])

    def test_negacion_dentro_de_una_transaccion_revertida_durante_una_peticion(self):
        pendientes = servicios.iniciar_pendientes()
        try:
            with transaction.atomic():
                registrar(self.gc, accion="periodo:cerrar", resultado="DENEGADO", detalle="negada")
                registrar(self.gc, accion="periodo:abrir", resultado="OK")
                raise RuntimeError("la operación se revierte")
        except RuntimeError:
            pass
        self.assertFalse(Evento.objects.exists())  # aún no: la transacción se revirtió
        servicios.insertar_pendientes(pendientes)  # lo hace el middleware al terminar la petición
        self.assertEqual(list(Evento.objects.values_list("accion", "resultado")), [("periodo:cerrar", "DENEGADO")])

    def test_rechaza_una_instantanea_de_transaccion(self):
        with self.assertRaisesMessage(DatabaseError, "READ COMMITTED"):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("INSERT INTO bitacora_evento (id, accion, resultado, roles, actor_nombre, recurso_tipo,"
                               " recurso_id, detalle, origen, hash, hash_anterior)"
                               " VALUES (gen_random_uuid(), 'x:y', 'OK', '[]', '', '', '', '', '', '', '')")

    def test_concurrencia_sin_bifurcaciones_ni_huecos(self):
        errores = []

        def escribir(n):
            try:
                for i in range(15):
                    with transaction.atomic():
                        registrar(None, accion="alerta:crear", recurso_id=f"{n}-{i}")
            except Exception as exc:  # pragma: no cover - se informa abajo
                errores.append(exc)
            finally:
                connections.close_all()

        hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(errores, [])
        secuencias = list(Evento.objects.order_by("secuencia").values_list("secuencia", flat=True))
        self.assertEqual(secuencias, list(range(1, 61)))
        self.assertEqual(Evento.objects.values("hash_anterior").distinct().count(), 60)
        self.assertEqual(servicios.verificar_cadena(), [])


class PermisosTests(ListasBase):
    """Matriz: GC·O8 L, AE·O8 L/X [C5]; TC, VF, AC y PF: —. RLS repite lo mismo en la base."""

    def setUp(self):
        super().setUp()
        self.evento = Evento.objects.get()  # el que crea ListasBase

    def test_motor(self):
        for usuario in (self.gc, self.ae):
            with self.subTest(usuario=usuario.username):
                self.assertEqual(reglas.decidir(pep.construir_solicitud(usuario, Accion.BITACORA_LEER,
                                                                        self.evento)).resultado, CONCEDE)
        for usuario in (self.tc, self.vf, self.ac, self.pf):
            with self.subTest(usuario=usuario.username):
                self.assertEqual(reglas.decidir(pep.construir_solicitud(usuario, Accion.BITACORA_LEER,
                                                                        self.evento)).resultado, NIEGA)
                with self.assertRaises(AccesoDenegado):
                    listar(usuario, "bitacora")
        gc_exporta = pep.construir_solicitud(self.gc, Accion.BITACORA_EXPORTAR, None, {"registros": 1})
        self.assertEqual(reglas.decidir(gc_exporta).resultado, NIEGA)  # GC lee, no exporta

    def test_rls_con_el_rol_de_la_aplicacion(self):
        with connection.cursor() as c:
            c.execute(f"CREATE ROLE {ROL} NOLOGIN NOSUPERUSER NOBYPASSRLS")
            c.execute(f"GRANT USAGE ON SCHEMA public TO {ROL}")
            c.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROL}")
        total = Evento.objects.count()

        def visibles(usuario):
            with connection.cursor() as c:
                c.execute(f"SET SESSION AUTHORIZATION {ROL}")
            rls.fijar_contexto(usuario_id=getattr(usuario, "pk", None))
            try:
                return Evento.objects.count()
            finally:
                with connection.cursor() as c:
                    c.execute("RESET SESSION AUTHORIZATION")
                rls.fijar_contexto()

        self.assertEqual(visibles(self.gc), total)
        self.assertEqual(visibles(self.ae), total)
        for usuario in (self.tc, self.vf, self.ac, self.pf, None):
            with self.subTest(usuario=getattr(usuario, "username", "anónimo")):
                self.assertEqual(visibles(usuario), 0)

    def test_rg4_las_lecturas_concedidas_quedan_registradas(self):
        from autorizacion import cliente

        with override_settings(VERDEAZUL_GANCHOS={"autorizar": "autorizacion.cliente.autorizar",
                                                  "registrar_evento": "bitacora.servicios.registrar_evento"}):
            cliente.autorizar(self.gc, Accion.FINCA_LEER, self.finca)
            cliente.autorizar(self.gc, Accion.PERIODO_LEER, self.periodo)  # O2: no es de RG4
        leidas = Evento.objects.filter(resultado="CONCEDIDO")
        self.assertEqual(list(leidas.values_list("accion", "recurso_id")), [(Accion.FINCA_LEER, str(self.finca.pk))])


class SellosTests(TestCase):
    """Sellos externos (Arquitectura §4.5): detectan lo que la cadena sola no puede."""

    def setUp(self):
        from bitacora import sellos

        self.sellos = sellos
        self.gc = con_rol("gerente", "GC")
        for i in range(3):
            registrar(self.gc, recurso_id=str(i))
        self.sello = sellos.sellar()

    def como_superusuario(self, *consultas):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role = replica")
            for consulta in consultas:
                cursor.execute(consulta)
            cursor.execute("SET LOCAL session_replication_role = origin")

    def test_sellar_y_verificar(self):
        from bitacora.models import SelloBitacora

        cabeza = Evento.objects.order_by("-secuencia").first()
        self.assertEqual((self.sello.secuencia, self.sello.hash), (cabeza.secuencia, cabeza.hash))
        self.assertEqual(self.sellos.verificar_sello(self.sello), [])
        self.assertEqual(self.sellos.verificar(), [])
        self.assertEqual(SelloBitacora.objects.count(), 1)

    def test_reescribir_toda_la_cadena_la_delata_el_sello(self):
        # Un superusuario cambia el evento 2 y recalcula los hashes del 2 y del 3: la cadena queda coherente...
        self.como_superusuario(
            "UPDATE bitacora_evento SET detalle = 'reescrito' WHERE secuencia = 2",
            "UPDATE bitacora_evento e SET hash = bitacora_hash(e.hash_anterior, bitacora_canonico(e)) WHERE secuencia = 2",
            "UPDATE bitacora_evento SET hash_anterior = (SELECT hash FROM bitacora_evento WHERE secuencia = 2)"
            " WHERE secuencia = 3",
            "UPDATE bitacora_evento e SET hash = bitacora_hash(e.hash_anterior, bitacora_canonico(e)) WHERE secuencia = 3",
        )
        self.assertEqual(servicios.verificar_cadena(), [])
        # ...pero no puede firmar un sello nuevo: el evento sellado ya no coincide.
        self.assertEqual(self.sellos.verificar(), [(3, "sello: el evento sellado cambió (cadena reescrita)")])

    def test_truncar_el_final_lo_delata_el_sello(self):
        self.como_superusuario("DELETE FROM bitacora_evento WHERE secuencia = 3")
        self.assertEqual(servicios.verificar_cadena(), [])  # sin huecos: se cortó el final
        self.assertIn("ya no existe", self.sellos.verificar()[0][1])

    def test_sello_alterado_o_de_otra_clave_no_vale(self):
        from bitacora.models import SelloBitacora
        from certificados.firma import nueva_clave

        self.como_superusuario(f"UPDATE bitacora_sellobitacora SET hash = repeat('a', 64) WHERE id = '{self.sello.pk}'")
        problemas = self.sellos.verificar_sello(SelloBitacora.objects.get(pk=self.sello.pk))
        self.assertIn("las columnas del sello no coinciden con lo firmado", problemas)
        privada, _ = nueva_clave()
        with override_settings(CERTIFICADO_CLAVE_PRIVADA=privada):
            ajeno = self.sellos.sellar()  # firmado con una clave que Verdeazul no publicó
        self.assertIn("el sello no lo firmó una clave de Verdeazul", self.sellos.verificar_sello(ajeno))

    def test_los_sellos_son_de_solo_insercion(self):
        for consulta in ("UPDATE bitacora_sellobitacora SET hash = 'x'", "DELETE FROM bitacora_sellobitacora"):
            with self.subTest(consulta=consulta), self.assertRaisesMessage(DatabaseError, "solo inserción"):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(consulta)

    def test_el_comando_publica_el_sello_y_no_sella_una_cadena_alterada(self):
        import io
        import json

        from django.core.management import CommandError, call_command

        salida = io.StringIO()
        call_command("sellar_bitacora", stdout=salida)
        publicado = json.loads(salida.getvalue())
        self.assertEqual(publicado["tipo"], "verdeazul.sello_bitacora")
        self.assertTrue(Evento.objects.filter(accion=Accion.BITACORA_SELLAR).exists())
        call_command("verificar_bitacora", stdout=io.StringIO())
        self.como_superusuario("UPDATE bitacora_evento SET detalle = 'x' WHERE secuencia = 1")
        with self.assertRaisesMessage(CommandError, "no se sella"):
            call_command("sellar_bitacora", stdout=io.StringIO(), stderr=io.StringIO())
        with self.assertRaisesMessage(CommandError, "ALTERADA"):
            call_command("verificar_bitacora", stdout=io.StringIO(), stderr=io.StringIO())

    def test_rls_solo_gc_y_ae_leen_y_solo_el_sistema_sella(self):
        from bitacora.models import SelloBitacora

        ac = con_rol("admin_ac", "AC")
        tc = con_rol("tecnica", "TC")
        with connection.cursor() as c:
            c.execute(f"CREATE ROLE {ROL} NOLOGIN NOSUPERUSER NOBYPASSRLS")
            c.execute(f"GRANT USAGE ON SCHEMA public TO {ROL}")
            c.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROL}")

        def como(usuario, fase=None):
            with connection.cursor() as c:
                c.execute(f"SET SESSION AUTHORIZATION {ROL}")
            rls.fijar_contexto(usuario_id=getattr(usuario, "pk", None), fase=fase)

        def volver():
            with connection.cursor() as c:
                c.execute("RESET SESSION AUTHORIZATION")
            rls.fijar_contexto()

        for usuario, visibles in ((self.gc, 1), (tc, 0), (ac, 0), (None, 0)):
            como(usuario)
            try:
                self.assertEqual(SelloBitacora.objects.count(), visibles)
            finally:
                volver()
        como(self.gc)
        try:
            with self.assertRaises(DatabaseError), transaction.atomic():
                self.sellos.sellar()  # un GC no sella: solo el proceso del sistema
        finally:
            volver()
