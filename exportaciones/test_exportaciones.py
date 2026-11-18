"""
Exportaciones (X, Persona 4) con el motor real: solo el AE en su ventana (C5), N = registros
reales, H3 (más de 500 en 24 h exige segundo factor reciente), marca de agua dentro del
archivo, evento en la bitácora, negaciones registradas y campos de lista blanca.
"""

import csv
import hashlib
import io
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import override_settings

from autorizacion import pep, reglas
from autorizacion.reglas import NIEGA
from autorizacion.test_listas import ListasBase
from bitacora.models import Evento
from catalogo import services as catalogo
from cuentas import services as cuentas
from cuentas.hbac import marca_de_agua
from cuentas.models import ActividadHBAC, TipoActividad
from cuentas.selectores import hoy
from exportaciones import servicios
from exportaciones.servicios import CONJUNTOS, exportar
from mediciones.models import Medicion
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, ReautenticacionRequerida

GANCHOS_REALES = {
    "autorizar": "autorizacion.cliente.autorizar",
    "usuarios_con_rol": "cuentas.roles.usuarios_con_rol",
    "registrar_evento": "bitacora.servicios.registrar_evento",
    "on_medicion_aprobada": "certificados.servicios.emitir",
}


def leer_csv(contenido: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(contenido.decode("utf-8"))))


class ExportacionBase(ListasBase):
    def setUp(self):
        super().setUp()
        parche = override_settings(VERDEAZUL_GANCHOS=GANCHOS_REALES)
        parche.enable()
        self.addCleanup(parche.disable)

    def con_mfa(self, usuario):
        marca = pep.peticion_actual.set(SimpleNamespace(user=usuario, session={}))
        self.addCleanup(pep.peticion_actual.reset, marca)
        parche = mock.patch("cuentas.mfa.edad_mfa", return_value=10.0)
        parche.start()
        self.addCleanup(parche.stop)


class ExportarTests(ExportacionBase):
    def test_ae_exporta_con_n_real_y_marca_de_agua_en_el_archivo(self):
        x = exportar(self.ae, "mediciones")
        filas = leer_csv(x.contenido)
        marca = x.marca
        self.assertTrue(marca.startswith(marca_de_agua(self.ae)[:40]))  # cuenta nominal y fecha (H3)
        self.assertIn(f"auditora (cuenta {self.ae.pk})", marca)
        self.assertEqual(filas[0], [f"# {marca}"])
        encabezado, datos, final = filas[2], filas[3:-1], filas[-1]
        self.assertEqual(encabezado[-1], "marca_de_agua")
        self.assertEqual(x.registros, Medicion.objects.count())
        self.assertEqual(len(datos), x.registros)
        self.assertTrue(all(fila[-1] == marca for fila in datos))
        self.assertIn(marca, final[0])
        self.assertEqual(x.sha256, hashlib.sha256(x.contenido).hexdigest())
        # El conteo de H3 y la bitácora llevan la MISMA cantidad que el archivo.
        actividad = ActividadHBAC.objects.get(sujeto=self.ae, tipo=TipoActividad.EXPORTACION)
        self.assertEqual((actividad.objeto, actividad.registros), ("O4", x.registros))
        evento = Evento.objects.get(accion=Accion.MEDICION_EXPORTAR, resultado="OK")
        self.assertEqual((evento.actor_id, evento.despues["registros"], evento.despues["sha256"]),
                         (self.ae.pk, x.registros, x.sha256))

    def test_todos_los_conjuntos_y_solo_campos_de_lista_blanca(self):
        self.con_mfa(self.ae)  # 14 exportaciones seguidas pasan de 500 registros en 24 h solo si hay muchos datos
        prohibidos = {"password", "llave_publica", "data", "clave_hash", "secret", "contenido"}
        for nombre, conjunto in CONJUNTOS.items():
            with self.subTest(conjunto=nombre):
                x = exportar(self.ae, nombre)
                encabezado = leer_csv(x.contenido)[2]
                self.assertEqual(encabezado, [*conjunto.campos, "marca_de_agua"])
                self.assertFalse(prohibidos & set(encabezado))
                self.assertEqual(x.registros, len(leer_csv(x.contenido)) - 4)

    def test_otros_roles_no_exportan_y_queda_la_negacion(self):
        for usuario in (self.gc, self.tc, self.vf, self.ac, self.pf):
            with self.subTest(usuario=usuario.username), self.assertRaises(AccesoDenegado):
                exportar(usuario, "mediciones")
        self.assertEqual(Evento.objects.filter(accion=Accion.MEDICION_EXPORTAR, resultado="DENEGADO").count(), 5)
        self.assertFalse(Evento.objects.filter(accion=Accion.MEDICION_EXPORTAR, resultado="OK").exists())
        self.assertFalse(ActividadHBAC.objects.filter(tipo=TipoActividad.EXPORTACION).exists())

    def test_ae_fuera_de_su_ventana_no_exporta(self):
        with mock.patch("cuentas.selectores.hoy", return_value=hoy() + timedelta(days=10)), \
                self.assertRaises(AccesoDenegado):
            exportar(self.ae, "fincas")
        self.assertTrue(Evento.objects.filter(accion=Accion.FINCA_EXPORTAR, resultado="DENEGADO").exists())

    def test_h3_mas_de_500_en_24_horas_exige_segundo_factor(self):
        cuentas.registrar_actividad(self.ae, TipoActividad.EXPORTACION, "O4", registros=500)
        with self.assertRaises(ReautenticacionRequerida):
            exportar(self.ae, "mediciones")  # 500 + N > 500 sin segundo factor reciente
        negada = Evento.objects.get(accion=Accion.MEDICION_EXPORTAR, resultado="DENEGADO")
        self.assertIn("ReautenticacionRequerida", negada.detalle)
        self.con_mfa(self.ae)
        x = exportar(self.ae, "mediciones")
        self.assertGreater(x.registros, 0)

    def test_sin_registros_el_motor_niega(self):
        sin_registros = pep.construir_solicitud(self.ae, Accion.MEDICION_EXPORTAR, None, {})
        self.assertEqual(reglas.decidir(sin_registros).resultado, NIEGA)
        con_registros = pep.construir_solicitud(self.ae, Accion.MEDICION_EXPORTAR, None, {"registros": 3})
        self.assertEqual(reglas.decidir(con_registros).resultado, "CONCEDE")

    def test_la_cantidad_debe_coincidir_con_lo_exportado(self):
        with mock.patch.object(servicios, "LIMITE_FILAS", 0):
            with self.assertRaises(Exception):
                exportar(self.ae, "mediciones")  # supera el tope por archivo: no se exporta nada
        self.assertFalse(Evento.objects.filter(accion=Accion.MEDICION_EXPORTAR, resultado="OK").exists())

    def test_inyeccion_de_formulas_csv(self):
        with override_settings(AUTORIZACION_PROVISIONAL_PERMISIVA=True,
                               VERDEAZUL_GANCHOS={"usuarios_con_rol": "cuentas.roles.usuarios_con_rol"}):
            catalogo.crear_finca(self.ac, '=HYPERLINK("http://x")', "-1+2", self.pf)
        filas = leer_csv(exportar(self.ae, "fincas").contenido)
        celdas = {celda for fila in filas[3:-1] for celda in fila}
        self.assertIn("'=HYPERLINK(\"http://x\")", celdas)
        self.assertIn("'-1+2", celdas)
        self.assertFalse(any(c.startswith(("=", "+", "-", "@")) for c in celdas))
