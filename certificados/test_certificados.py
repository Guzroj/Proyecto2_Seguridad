"""
Certificados (O6, Persona 4): emisión firmada dentro de la aprobación, reversión, doble
emisión, P2 (revocar y reemitir o mantener), verificación y manipulación, C4 con el motor
real, disparadores con SQL directo y RLS con el rol de la aplicación.
"""

import base64
import copy
import io
import json
from contextlib import redirect_stdout
from decimal import Decimal
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import override_settings

from autorizacion import pep, reglas
from autorizacion.listas import listar
from autorizacion.reglas import CONCEDE, FUERA_DE_ALCANCE, NIEGA
from autorizacion.test_cliente import con_rol
from autorizacion.test_integracion_o1_o3 import EscenarioBase
from autorizacion.test_integracion_o7_o9 import GANCHOS as GANCHOS_MOTOR
from bitacora.models import Evento
from catalogo import services as catalogo
from certificados import firma, formato
from certificados.models import Certificado
from certificados.servicios import emitir
from cuentas import rls
from cuentas.test_rls_dominio import RLSMedicionesBase
from mediciones import services as mediciones
from mediciones.models import Medicion
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, FueraDeAlcance, TransicionInvalida

GANCHOS_P4 = {
    "usuarios_con_rol": "cuentas.roles.usuarios_con_rol",
    "registrar_evento": "bitacora.servicios.registrar_evento",
    "on_medicion_aprobada": "certificados.servicios.emitir",
}
# Servicios con autorización permisiva (el motor se prueba aparte) y bitácora y emisión REALES.
P4 = override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=True, VERDEAZUL_GANCHOS=GANCHOS_P4)


def decidir(usuario, accion, recurso=None, contexto=None):
    return reglas.decidir(pep.construir_solicitud(usuario, accion, recurso, contexto))


@P4
class EmisionBase(EscenarioBase):
    def setUp(self):
        super().setUp()
        self.datos_pf = catalogo.registrar_datos_propietario(
            self.ac, self.pf, nombre_completo="Patricia Fallas Mora", identificacion="1-1111-1111")

    def aprobar(self):
        self.enviar()
        self.medicion = mediciones.aprobar_medicion(self.vf, self.medicion)
        return self.medicion

    def enmendar(self, **datos):
        valores = {"numero_arboles": 120, "dap_promedio_cm": "22.5", "altura_promedio_m": "11", **datos}
        enmienda = mediciones.solicitar_enmienda(self.tc, self.medicion, justificacion="Recuento en campo", **valores)
        enmienda = mediciones.autorizar_enmienda(self.gc, enmienda)
        version = Medicion.objects.get(pk=enmienda.version_id)
        return mediciones.aprobar_medicion(version.verificador, version)

    def vigente(self):
        return Certificado.objects.get(parcela=self.parcela, periodo=self.periodo, estado="EMITIDO")


class EmisionTests(EmisionBase):
    def test_aprobar_emite_un_certificado_firmado(self):
        m = self.aprobar()
        c = self.vigente()
        self.assertEqual((c.medicion_id, c.toneladas_co2, c.propietario_id), (m.pk, m.toneladas_co2, self.pf.pk))
        self.assertEqual(c.parcela_version_id, m.parcela_version_id)
        r = formato.verificar(c.archivo(), firma.huellas_confiables())
        self.assertTrue(r["valido"], r["errores"])
        contenido = r["contenido"]
        self.assertEqual(contenido["toneladas_co2"], str(m.toneladas_co2))
        self.assertEqual(contenido["propietario"]["nombre_completo"], "Patricia Fallas Mora")
        self.assertEqual(contenido["certificado"]["id"], str(c.pk))
        self.assertEqual(contenido["emisor"]["huella_clave"], firma.huella_actual())
        # La clave pública viaja en el documento; la privada en ningún lado.
        self.assertEqual(r["huella_clave"], c.huella_clave)
        self.assertNotIn(settings_clave(), c.archivo())
        evento = Evento.objects.get(accion=Accion.CERTIFICADO_EMITIR)
        self.assertEqual((evento.actor_id, evento.recurso_id, evento.resultado), (self.vf.pk, str(c.pk), "OK"))
        self.assertTrue(Evento.objects.filter(accion=Accion.MEDICION_APROBAR, resultado="OK").exists())

    def test_contenido_firmado_es_determinista(self):
        self.aprobar()
        c = self.vigente()
        self.assertEqual(formato.canonico(c.contenido_dict).decode("utf-8"), c.contenido)
        self.assertEqual(json.loads(c.archivo()), c.documento())

    def test_si_la_emision_falla_la_aprobacion_se_revierte(self):
        self.enviar()
        with override_settings(CERTIFICADO_CLAVE_PRIVADA=""), self.assertRaises(TransicionInvalida):
            mediciones.aprobar_medicion(self.vf, self.medicion)
        self.assertEqual(Medicion.objects.get(pk=self.medicion.pk).estado, "ENVIADA")
        self.assertFalse(Certificado.objects.exists())
        self.assertFalse(Evento.objects.filter(accion=Accion.MEDICION_APROBAR, resultado="OK").exists())
        negada = Evento.objects.get(accion=Accion.MEDICION_APROBAR, resultado="DENEGADO")
        self.assertIn("clave de firma", negada.detalle)
        # Cualquier excepción de la emisión revierte igual (no solo las del dominio).
        with mock.patch("certificados.servicios._emitir_nuevo", side_effect=RuntimeError("falla")), \
                self.assertRaises(RuntimeError):
            mediciones.aprobar_medicion(self.vf, self.medicion)
        self.assertEqual(Medicion.objects.get(pk=self.medicion.pk).estado, "ENVIADA")

    def test_sin_datos_del_propietario_no_se_certifica_ni_se_aprueba(self):
        with mock.patch("autorizacion.procesos.datos_propietario_para_certificado",
                        side_effect=FueraDeAlcance("No encontrado.")):
            self.enviar()
            with self.assertRaises(TransicionInvalida):
                mediciones.aprobar_medicion(self.vf, self.medicion)
        self.assertEqual(Medicion.objects.get(pk=self.medicion.pk).estado, "ENVIADA")

    def test_doble_emision_bloqueada(self):
        m = self.aprobar()
        with self.assertRaises(TransicionInvalida):
            emitir(m)  # el servicio lo niega
        self.assertEqual(Certificado.objects.filter(estado="EMITIDO").count(), 1)
        # y la base también, aunque alguien se salte el servicio (índice único parcial)
        copia = Certificado.objects.get()
        copia.pk = None
        copia.id = __import__("uuid").uuid4()
        contenido = json.loads(copia.contenido)
        contenido["certificado"]["id"] = str(copia.id)
        copia.contenido = formato.canonico(contenido).decode()
        with self.assertRaises(IntegrityError), transaction.atomic():
            copia.save(force_insert=True)


class P2Tests(EmisionBase):
    def test_p2_enmienda_que_cambia_toneladas_revoca_y_reemite(self):
        self.aprobar()
        original = self.vigente()
        version = self.enmendar(numero_arboles=150)
        self.assertNotEqual(version.toneladas_co2, original.toneladas_co2)
        anterior = Certificado.objects.get(pk=original.pk)
        nuevo = self.vigente()
        self.assertEqual(anterior.estado, "REVOCADO")
        self.assertEqual(anterior.revocado_por_medicion_id, version.pk)
        self.assertEqual((nuevo.reemplaza_a_id, nuevo.medicion_id, nuevo.toneladas_co2),
                         (anterior.pk, version.pk, version.toneladas_co2))
        self.assertEqual(json.loads(nuevo.contenido)["certificado"]["reemplaza_a"], str(anterior.pk))
        # El histórico se conserva y sigue verificando (la firma no cambió; el estado vive en el registro).
        self.assertTrue(formato.verificar(anterior.archivo(), firma.huellas_confiables())["valido"])
        self.assertEqual(Certificado.objects.count(), 2)
        self.assertEqual(Medicion.objects.get(pk=self.medicion.pk).estado, "APROBADA")  # la original no cambia
        self.assertTrue(Evento.objects.filter(accion=Accion.CERTIFICADO_REVOCAR, recurso_id=str(anterior.pk)).exists())

    def test_p2_enmienda_sin_cambio_de_toneladas_conserva_el_certificado(self):
        self.aprobar()
        original = self.vigente()
        self.enmendar(observaciones="Corrección administrativa del nombre del sitio")
        self.assertEqual(self.vigente().pk, original.pk)
        self.assertEqual(Certificado.objects.count(), 1)
        self.assertTrue(Evento.objects.filter(accion=Accion.CERTIFICADO_MANTENER, recurso_id=str(original.pk)).exists())

    def test_p2_dos_enmiendas_encadenan_los_reemplazos(self):
        self.aprobar()
        primero = self.vigente()
        self.medicion = self.enmendar(numero_arboles=150)
        segundo = self.vigente()
        self.gc = con_rol("gerente2", "GC")  # C3: el GC de la primera enmienda ya es actor de la cadena
        con_rol("verificador3", "VF")         # D4: vf y vf2 ya son actores de la cadena
        self.enmendar(numero_arboles=170)
        tercero = self.vigente()
        self.assertEqual((segundo.reemplaza_a_id, tercero.reemplaza_a_id), (primero.pk, segundo.pk))
        self.assertEqual(Certificado.objects.filter(estado="REVOCADO").count(), 2)


class ManipulacionTests(EmisionBase):
    def setUp(self):
        super().setUp()
        self.aprobar()
        self.documento = self.vigente().documento()
        self.huellas = firma.huellas_confiables()

    def test_el_original_verifica(self):
        self.assertTrue(formato.verificar(formato.serializar(self.documento), self.huellas)["valido"])

    def test_cambiar_cualquier_dato_firmado_invalida(self):
        cambios = [("toneladas_co2", "9999.0000"), ("propietario", {"cuenta": self.pf2.pk}),
                   ("metodo", "otro"), ("periodo", None)]
        for campo, valor in cambios:
            with self.subTest(campo=campo):
                alterado = copy.deepcopy(self.documento)
                alterado["contenido"][campo] = valor
                r = formato.verificar(formato.serializar(alterado), self.huellas)
                self.assertFalse(r["valido"])
                self.assertFalse(r["integro"])

    def test_firma_o_clave_alteradas_invalidan(self):
        alterado = copy.deepcopy(self.documento)
        firma_bytes = bytearray(base64.b64decode(alterado["firma"]["valor"]))
        firma_bytes[0] ^= 1
        alterado["firma"]["valor"] = base64.b64encode(bytes(firma_bytes)).decode()
        self.assertFalse(formato.verificar(alterado, self.huellas)["valido"])
        otra = copy.deepcopy(self.documento)
        otra["firma"]["clave_publica"] = formato.b64(formato.publica_cruda(Ed25519PrivateKey.generate().public_key()))
        self.assertFalse(formato.verificar(otra, self.huellas)["valido"])

    def test_documento_falso_firmado_con_otra_clave_es_integro_pero_no_de_verdeazul(self):
        atacante = Ed25519PrivateKey.generate()
        contenido = copy.deepcopy(self.documento["contenido"])
        contenido["toneladas_co2"] = "9999.0000"
        contenido["emisor"]["huella_clave"] = formato.huella(formato.publica_cruda(atacante.public_key()))
        falso = formato.firmar(contenido, atacante)
        r = formato.verificar(falso, self.huellas)
        self.assertTrue(r["integro"])          # la firma corresponde a SU clave incrustada...
        self.assertFalse(r["confiable"])       # ...pero esa clave no es de Verdeazul
        self.assertFalse(r["valido"])

    def test_json_con_claves_duplicadas_o_grande_se_rechaza(self):
        texto = formato.serializar(self.documento)
        duplicado = texto.replace('"formato":', '"formato": "x", "formato":', 1)
        self.assertFalse(formato.verificar(duplicado, self.huellas)["valido"])
        self.assertFalse(formato.verificar(b"x" * (formato.TAMANO_MAXIMO + 1), self.huellas)["valido"])
        self.assertFalse(formato.verificar("no es json", self.huellas)["valido"])

    def test_verificador_independiente_sin_django(self):
        import os
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(formato.serializar(self.documento))
        self.addCleanup(os.unlink, f.name)
        salida = io.StringIO()
        with redirect_stdout(salida):
            self.assertEqual(formato.main([f.name, "--huella", firma.huella_actual()]), 0)
            self.assertEqual(formato.main([f.name, "--huella", "0" * 64]), 1)   # clave no publicada
            self.assertEqual(formato.main([f.name]), 1)                         # sin ancla de confianza
        self.assertIn("VÁLIDO", salida.getvalue())


def settings_clave() -> str:
    from django.conf import settings

    return settings.CERTIFICADO_CLAVE_PRIVADA


class DisparadoresTests(EmisionBase):
    """La base revalida aunque alguien escriba con SQL directo (como dueño, sin RLS)."""

    def setUp(self):
        super().setUp()
        self.aprobar()
        self.c = self.vigente()

    def sql(self, consulta, params=()):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(consulta, params)

    def test_un_emitido_no_se_modifica_ni_se_borra(self):
        for consulta in ("UPDATE certificados_certificado SET toneladas_co2 = 1 WHERE id = %s",
                         "UPDATE certificados_certificado SET contenido = '{}' WHERE id = %s",
                         "UPDATE certificados_certificado SET estado = 'REVOCADO', revocado_en = now(), "
                         "revocado_por_medicion_id = medicion_id WHERE id = %s",   # la original no revoca
                         "DELETE FROM certificados_certificado WHERE id = %s"):
            with self.subTest(consulta=consulta), self.assertRaises(DatabaseError):
                self.sql(consulta, [str(self.c.pk)])
        self.assertEqual(Certificado.objects.get(pk=self.c.pk).estado, "EMITIDO")

    COPIA = ("INSERT INTO certificados_certificado (id, parcela_id, periodo_id, parcela_version_id, medicion_id,"
             " propietario_id, toneladas_co2, estado, emitido_en, contenido, firma, clave_publica, huella_clave,"
             " motivo_revocacion) SELECT gen_random_uuid(), parcela_id, periodo_id, parcela_version_id,"
             " medicion_id, propietario_id, toneladas_co2 * {factor}, 'EMITIDO', now(), contenido, firma,"
             " clave_publica, huella_clave, '' FROM certificados_certificado WHERE id = %s")

    def test_no_se_inserta_un_certificado_que_no_corresponde_a_la_medicion(self):
        with self.assertRaisesMessage(DatabaseError, "no coincide con la medición"):
            self.sql(self.COPIA.format(factor=10), [str(self.c.pk)])

    def test_con_sujeto_solo_el_verificador_que_aprobo_emite(self):
        rls.fijar_contexto(usuario_id=self.gc.pk)
        self.addCleanup(rls.fijar_contexto)
        with self.assertRaisesMessage(DatabaseError, "C2"):
            self.sql(self.COPIA.format(factor=1), [str(self.c.pk)])


class LecturaC4Tests(EmisionBase):
    """C4 con el motor real: el propietario ve los suyos, incluidos los revocados; nadie más fuera de GC y AE."""

    def setUp(self):
        super().setUp()
        self.aprobar()
        self.medicion = self.enmendar(numero_arboles=150)
        self.revocado = Certificado.objects.get(estado="REVOCADO")
        self.vigente_ = self.vigente()

    def test_pf_lee_los_suyos_y_otro_pf_recibe_404(self):
        for c in (self.vigente_, self.revocado):
            with self.subTest(estado=c.estado):
                self.assertEqual(decidir(self.pf, Accion.CERTIFICADO_LEER, c).resultado, CONCEDE)
                self.assertEqual(decidir(self.pf2, Accion.CERTIFICADO_LEER, c).resultado, FUERA_DE_ALCANCE)  # V8

    def test_gc_lee_y_tc_vf_ac_no(self):
        self.assertEqual(decidir(self.gc, Accion.CERTIFICADO_LEER, self.vigente_).resultado, CONCEDE)
        for usuario in (self.tc, self.vf, self.ac):
            with self.subTest(usuario=usuario.username):
                self.assertEqual(decidir(usuario, Accion.CERTIFICADO_LEER, self.vigente_).resultado, NIEGA)
        self.assertEqual(decidir(self.gc, Accion.CERTIFICADO_EXPORTAR, None, {"registros": 1}).resultado, NIEGA)

    def test_nadie_emite_ni_revoca_a_mano(self):
        for usuario in (self.gc, self.vf, self.ac, self.pf):
            for accion in (Accion.CERTIFICADO_EMITIR, Accion.CERTIFICADO_REVOCAR):
                with self.subTest(usuario=usuario.username, accion=accion):
                    self.assertEqual(decidir(usuario, accion, self.vigente_).resultado, NIEGA)


class RLSCertificadosTests(RLSMedicionesBase):
    """Con el rol de la aplicación (como app_runtime en Neon) y las funciones de un dueño sin superusuario."""

    def setUp(self):
        super().setUp()
        parche = override_settings(VERDEAZUL_GANCHOS={**GANCHOS_MOTOR, **GANCHOS_P4,
                                                      "autorizar": "autorizacion.cliente.autorizar"})
        parche.enable()
        self.addCleanup(parche.disable)
        from cuentas.test_rls_dominio import DUENO

        with connection.cursor() as c:
            for funcion in ("bitacora_encadenar()", "certificado_estado_publico(uuid, text)"):
                c.execute(f"ALTER FUNCTION {funcion} OWNER TO {DUENO}")
            c.execute(f"GRANT EXECUTE ON FUNCTION certificado_estado_publico(uuid, text) TO {self.rol()}")
        self.original = Certificado.objects.get(estado="EMITIDO")  # ListasBase emitió el de la medición aprobada

    @staticmethod
    def rol():
        from cuentas.test_rls import ROL

        return ROL

    def test_lectura_por_rol(self):
        todos = {self.original.pk}
        casos = [(self.pf, todos), (self.pf2, set()), (self.tc, set()), (self.vf, set()), (self.ac, set()),
                 (self.gc, todos), (self.ae, todos)]
        for usuario, esperados in casos:
            with self.subTest(usuario=usuario.username):
                self.assertEqual(self.visibles(Certificado, usuario), esperados)
        self.assertEqual(self.visibles(Certificado), set())

    def test_nadie_escribe_fuera_de_la_emision(self):
        for usuario in (self.gc, self.vf, self.pf, self.ac):
            with self.subTest(usuario=usuario.username), self.como(usuario):
                self.assertEqual(self.sql("UPDATE certificados_certificado SET motivo_revocacion = 'x'"), 0)
                self.assertEqual(self.sql("DELETE FROM certificados_certificado"), 0)

    def test_emision_p2_como_la_aplicacion(self):
        from mediciones.models import Enmienda

        enmienda = Enmienda.objects.get(pk=self.enmienda.pk)
        with self.como(self.gc):
            self.con_mfa(self.gc)
            enmienda = mediciones.autorizar_enmienda(self.gc, enmienda)
        version = Medicion.objects.get(pk=enmienda.version_id)
        with self.como(self.vf2):
            self.con_mfa(self.vf2)
            mediciones.aprobar_medicion(self.vf2, version)
            self.assertEqual(Certificado.objects.count(), 0)  # fuera de la emisión, el VF no ve certificados
        nuevo = Certificado.objects.get(estado="EMITIDO")
        self.assertEqual((Certificado.objects.get(pk=self.original.pk).estado, nuevo.reemplaza_a_id),
                         ("REVOCADO", self.original.pk))
        self.assertTrue(formato.verificar(nuevo.archivo(), firma.huellas_confiables())["valido"])
        self.assertEqual(nuevo.toneladas_co2, version.toneladas_co2)
        self.assertEqual(Decimal(json.loads(nuevo.contenido)["toneladas_co2"]), version.toneladas_co2)

    def test_estado_publico_solo_con_id_y_firma(self):
        with self.como():  # anónimo
            with connection.cursor() as c:
                c.execute("SELECT estado FROM certificado_estado_publico(%s, %s)",
                          [str(self.original.pk), self.original.firma])
                self.assertEqual(c.fetchone(), ("EMITIDO",))
                c.execute("SELECT estado FROM certificado_estado_publico(%s, %s)", [str(self.original.pk), "x"])
                self.assertIsNone(c.fetchone())
            self.assertEqual(Certificado.objects.count(), 0)

    def test_listas_del_pf_y_negacion_a_tc(self):
        self.assertEqual(set(listar(self.pf, "certificados").values_list("pk", flat=True)), {self.original.pk})
        self.assertEqual(set(listar(self.pf2, "certificados").values_list("pk", flat=True)), set())
        with self.assertRaises(AccesoDenegado):
            listar(self.tc, "certificados")
