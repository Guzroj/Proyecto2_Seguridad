"""
Paso 14a · Condiciones de O1–O3 (C4, C6, C8, C9, C13, C19, C20) con atributos
armados a mano. Python puro: corre en Django y en el contenedor del autorizador.
"""

import unittest

from autorizacion import reglas
from autorizacion.reglas import CONCEDE, FUERA_DE_ALCANCE, NIEGA

S = 7  # id del sujeto
OTRO = 99


def decidir(accion, rol, recurso=None, contexto=None, sujeto=S):
    return reglas.decidir({"sujeto": {"id": sujeto, "estado": "ACTIVA", "roles": [rol], "mfa_edad": None},
                           "accion": accion, "recurso": recurso, "contexto": contexto or {}})


def finca(propietario=OTRO, tecnicos=(), verificadores=(), estado="ACTIVA", tipo="finca"):
    return {"tipo": tipo, "id": 1, "estado": estado, "propietario": propietario,
            "tecnicos_vigentes": list(tecnicos), "verificadores": list(verificadores)}


class Base(unittest.TestCase):
    def assertDecision(self, decision, resultado, regla=None):
        self.assertEqual(decision.resultado, resultado, decision.motivo)
        if regla:
            self.assertEqual(decision.regla, regla)


class C4Tests(Base):
    def test_o1a_y_o1b_solo_lo_propio(self):
        for accion, recurso in (("finca:leer", finca(propietario=S)), ("parcela:leer", finca(propietario=S, tipo="parcela")),
                                ("propietario:leer", {"tipo": "datospropietario", "id": 1, "propietario": S})):
            with self.subTest(accion=accion):
                self.assertDecision(decidir(accion, "PF", recurso), CONCEDE, "C4")
                ajeno = {**recurso, "propietario": OTRO}
                self.assertDecision(decidir(accion, "PF", ajeno), FUERA_DE_ALCANCE, "C4")

    def test_o4_solo_aprobadas(self):
        medicion = {"tipo": "medicion", "id": "m", "propietario": S, "estado": "APROBADA"}
        self.assertDecision(decidir("medicion:leer", "PF", medicion), CONCEDE)
        for estado in ("BORRADOR", "ENVIADA", "DEVUELTA"):
            with self.subTest(estado=estado):
                self.assertDecision(decidir("medicion:leer", "PF", {**medicion, "estado": estado}), FUERA_DE_ALCANCE)

    def test_o6_emitidos_y_revocados(self):
        certificado = {"tipo": "certificado", "id": 1, "propietario": S}
        for estado, resultado in (("EMITIDO", CONCEDE), ("REVOCADO", CONCEDE), ("ANULADO", FUERA_DE_ALCANCE)):
            with self.subTest(estado=estado):
                self.assertDecision(decidir("certificado:leer", "PF", {**certificado, "estado": estado}), resultado)

    def test_sin_atributos_se_niega(self):
        self.assertDecision(decidir("finca:leer", "PF"), NIEGA, "RG5")
        self.assertDecision(decidir("finca:leer", "PF", {"tipo": "finca", "id": 1}), NIEGA, "RG5")
        self.assertDecision(decidir("certificado:leer", "PF", {"tipo": "certificado", "id": 1, "propietario": S}),
                            NIEGA, "RG5")

    def test_pf_no_tiene_o2_ni_o3(self):
        self.assertDecision(decidir("periodo:leer", "PF", {"tipo": "periodo", "id": 1}), NIEGA, "RG1")


class C6C8C10Tests(Base):
    def test_c6_tc_lee_la_finca_que_tiene_asignada(self):
        for tipo in ("finca", "parcela"):
            with self.subTest(tipo=tipo):
                d = decidir(f"{tipo}:leer", "TC", finca(tecnicos=[S], tipo=tipo))
                self.assertDecision(d, CONCEDE, "C6")
                self.assertEqual(d.hbac, ("H1", "H2"))
                self.assertDecision(decidir(f"{tipo}:leer", "TC", finca(tecnicos=[OTRO], tipo=tipo)),
                                    FUERA_DE_ALCANCE, "C6")  # V1

    def test_c6_no_da_datos_personales(self):
        self.assertDecision(decidir("propietario:leer", "TC", {"tipo": "datospropietario", "id": 1}), NIEGA, "RG1")

    def test_c8_tc_lee_sus_asignaciones(self):
        asignacion = {"tipo": "asignacion", "id": 1, "tecnico": S}
        self.assertDecision(decidir("asignacion:leer", "TC", asignacion), CONCEDE, "C8")
        self.assertDecision(decidir("asignacion:leer", "TC", {**asignacion, "tecnico": OTRO}), FUERA_DE_ALCANCE)


class C9Tests(Base):
    def test_o4_verificador_asignado_o_actor(self):
        medicion = {"tipo": "medicion", "id": "m", "verificador": S, "actores": []}
        self.assertDecision(decidir("medicion:leer", "VF", medicion), CONCEDE, "C9")
        self.assertDecision(decidir("medicion:leer", "VF", {**medicion, "verificador": OTRO, "actores": [S]}), CONCEDE)
        self.assertDecision(decidir("medicion:leer", "VF", {**medicion, "verificador": OTRO}), FUERA_DE_ALCANCE, "C9")

    def test_o1a_finca_de_una_medicion_suya(self):
        self.assertDecision(decidir("finca:leer", "VF", finca(verificadores=[S])), CONCEDE, "C9")
        self.assertDecision(decidir("finca:leer", "VF", finca(verificadores=[OTRO])), FUERA_DE_ALCANCE)

    def test_sin_o1b_ni_o3(self):
        self.assertDecision(decidir("propietario:leer", "VF", {"tipo": "datospropietario", "id": 1}), NIEGA, "RG1")
        self.assertDecision(decidir("asignacion:leer", "VF", {"tipo": "asignacion", "id": 1}), NIEGA, "RG1")


class C13Tests(Base):
    def periodo(self, estado, pendientes=0):
        return {"tipo": "periodo", "id": 1, "estado": estado, "pendientes": pendientes}

    def test_abrir(self):
        self.assertDecision(decidir("periodo:abrir", "GC", None, {"nombre": "2026-A"}), CONCEDE, "C13")
        self.assertDecision(decidir("periodo:abrir", "AC"), NIEGA, "RG1")

    def test_pasar_a_verificacion_solo_desde_abierto(self):
        self.assertDecision(decidir("periodo:pasar_verificacion", "GC", self.periodo("ABIERTO")), CONCEDE)
        for estado in ("EN_VERIFICACION", "CERRADO"):
            with self.subTest(estado=estado):
                self.assertDecision(decidir("periodo:pasar_verificacion", "GC", self.periodo(estado)), NIEGA, "C13")

    def test_cerrar_sin_pendientes(self):
        self.assertDecision(decidir("periodo:cerrar", "GC", self.periodo("EN_VERIFICACION")), CONCEDE)
        self.assertDecision(decidir("periodo:cerrar", "GC", self.periodo("EN_VERIFICACION", 2)), NIEGA, "C13")  # V16
        for estado in ("ABIERTO", "CERRADO"):
            with self.subTest(estado=estado):
                self.assertDecision(decidir("periodo:cerrar", "GC", self.periodo(estado)), NIEGA, "C13")

    def test_sin_atributos(self):
        self.assertDecision(decidir("periodo:cerrar", "GC", {"tipo": "periodo", "id": 1, "estado": "EN_VERIFICACION"}),
                            NIEGA, "RG5")
        self.assertDecision(decidir("periodo:cerrar", "GC"), NIEGA, "RG5")


class C19Tests(Base):
    def contexto(self, roles=("TC",), estado_periodo="ABIERTO", tecnico=20, propietario=OTRO):
        return {"tecnico": {"tipo": "usuario", "id": tecnico, "estado": "ACTIVA", "roles": list(roles)},
                "finca": finca(propietario=propietario),
                "periodo": {"tipo": "periodo", "id": 1, "estado": estado_periodo}}

    def test_crear(self):
        self.assertDecision(decidir("asignacion:crear", "AC", None, self.contexto()), CONCEDE, "C19")
        negadas = [self.contexto(roles=("VF",)), self.contexto(roles=()), self.contexto(estado_periodo="CERRADO"),
                   self.contexto(tecnico=20, propietario=20)]  # V17: el técnico es el propietario
        for contexto in negadas:
            with self.subTest(contexto=contexto):
                self.assertDecision(decidir("asignacion:crear", "AC", None, contexto), NIEGA, "C19")

    def test_crear_sin_contexto(self):
        self.assertDecision(decidir("asignacion:crear", "AC", None, {"tecnico": {"id": 1}}), NIEGA, "RG5")

    def test_terminar(self):
        asignacion = {"tipo": "asignacion", "id": 1, "tecnico": 20, "propietario": OTRO, "periodo_estado": "ABIERTO"}
        self.assertDecision(decidir("asignacion:terminar", "AC", asignacion), CONCEDE, "C19")
        self.assertDecision(decidir("asignacion:terminar", "AC", {**asignacion, "periodo_estado": "CERRADO"}), NIEGA)
        self.assertDecision(decidir("asignacion:terminar", "AC", {**asignacion, "propietario": 20}), NIEGA)

    def test_solo_ac(self):
        self.assertDecision(decidir("asignacion:crear", "GC", None, self.contexto()), NIEGA, "RG1")


class C20Tests(Base):
    def test_objeto_vigente(self):
        for accion in ("finca:modificar", "finca:baja", "parcela:crear"):
            with self.subTest(accion=accion):
                self.assertDecision(decidir(accion, "AC", finca()), CONCEDE, "C20")
                self.assertDecision(decidir(accion, "AC", finca(estado="BAJA")), NIEGA, "C20")
        parcela = finca(tipo="parcela")
        self.assertDecision(decidir("parcela:modificar_geometria", "AC", parcela), CONCEDE)
        self.assertDecision(decidir("parcela:baja", "AC", {**parcela, "estado": "BAJA"}), NIEGA)

    def test_crear_sin_objeto_previo(self):
        self.assertDecision(decidir("finca:crear", "AC", None, {"propietario": {"tipo": "usuario", "id": 3}}), CONCEDE)
        self.assertDecision(decidir("propietario:registrar_datos", "AC", None, {"propietario": {"id": 3}}), CONCEDE)

    def test_solo_ac(self):
        for rol in ("GC", "TC", "PF"):
            with self.subTest(rol=rol):
                self.assertDecision(decidir("finca:modificar", rol, finca(propietario=S)), NIEGA, "RG1")


# ===========================================================================
# Paso 14b · O4–O6
# ===========================================================================
def medicion(**cambios):
    base = {"tipo": "medicion", "id": "m1", "estado": "BORRADOR", "autor": S, "autor_original": S,
            "verificador": None, "version_de": None, "actores": [S], "actores_cadena": [S], "tecnicos": [S],
            "asignados_vigentes": [S], "propietario": OTRO, "periodo_estado": "ABIERTO", "enmienda_estado": None,
            "enmienda_en_curso": False, "ultima_aprobada": None}
    return {**base, **cambios}


def decidir_mfa(accion, rol, recurso=None, contexto=None, mfa_edad=None, sujeto=S):
    return reglas.decidir({"sujeto": {"id": sujeto, "estado": "ACTIVA", "roles": [rol], "mfa_edad": mfa_edad},
                           "accion": accion, "recurso": recurso, "contexto": contexto or {}})


class C1Tests(Base):
    def test_crear(self):
        ctx = {"periodo": {"tipo": "periodo", "id": 1, "estado": "ABIERTO"}, "asignados_vigentes": [S]}
        parcela = finca(tipo="parcela")
        self.assertDecision(decidir("medicion:crear", "TC", parcela, ctx), CONCEDE, "C1")
        self.assertDecision(decidir("medicion:crear", "TC", parcela, {**ctx, "asignados_vigentes": [OTRO]}),
                            FUERA_DE_ALCANCE, "C1")
        cerrado = {**ctx, "periodo": {"tipo": "periodo", "id": 1, "estado": "EN_VERIFICACION"}}
        self.assertDecision(decidir("medicion:crear", "TC", parcela, cerrado), NIEGA, "C1")

    def test_modificar_y_enviar(self):
        concedidos = [medicion(), medicion(estado="DEVUELTA"), medicion(estado="DEVUELTA", periodo_estado="EN_VERIFICACION")]
        negados = [medicion(periodo_estado="EN_VERIFICACION"),               # BORRADOR fuera de ABIERTO
                   medicion(estado="ENVIADA"), medicion(version_de="m0"), medicion(asignados_vigentes=[])]
        for accion in ("medicion:modificar", "medicion:enviar"):
            for m in concedidos:
                with self.subTest(accion=accion, m=m["estado"]):
                    self.assertDecision(decidir(accion, "TC", m), CONCEDE, "C1")
            for m in negados:
                with self.subTest(accion=accion, negado=m):
                    self.assertDecision(decidir(accion, "TC", m), NIEGA, "C1")
            with self.subTest(accion=accion, ajena=True):
                self.assertDecision(decidir(accion, "TC", medicion(autor=OTRO)), FUERA_DE_ALCANCE, "C1")

    def test_v2_h4_aprobada_o_periodo_cerrado_no_se_modifica_nunca(self):
        for m in (medicion(estado="APROBADA"), medicion(estado="DEVUELTA", periodo_estado="CERRADO")):
            with self.subTest(m=m["estado"]):
                self.assertDecision(decidir("medicion:modificar", "TC", m), NIEGA, "H4")
        # Fuera de su alcance sigue siendo 404 (no revela el estado de una medición ajena).
        self.assertDecision(decidir("medicion:modificar", "TC", medicion(estado="APROBADA", autor=OTRO)),
                            FUERA_DE_ALCANCE)

    def test_c10_tc_lee_sus_mediciones(self):
        self.assertDecision(decidir("medicion:leer", "TC", medicion()), CONCEDE, "C10")
        self.assertDecision(decidir("medicion:leer", "TC", medicion(autor=OTRO)), FUERA_DE_ALCANCE, "C10")


class C2Tests(Base):
    def enviada(self, **cambios):
        base = dict(estado="ENVIADA", autor=OTRO, autor_original=OTRO, verificador=S, actores=[OTRO],
                    actores_cadena=[OTRO], tecnicos=[OTRO], asignados_vigentes=[OTRO])
        return medicion(**{**base, **cambios})

    def test_concede_con_segundo_factor_reciente(self):
        for accion in ("medicion:aprobar", "medicion:devolver"):
            with self.subTest(accion=accion):
                self.assertDecision(decidir_mfa(accion, "VF", self.enviada(), mfa_edad=30), CONCEDE, "C2")
                self.assertDecision(decidir_mfa(accion, "VF", self.enviada(), mfa_edad=None), "REAUTENTICAR", "C2")
                self.assertDecision(decidir_mfa(accion, "VF", self.enviada(), mfa_edad=301), "REAUTENTICAR")

    def test_negaciones(self):
        casos = {
            "no enviada": self.enviada(estado="DEVUELTA"),
            "periodo cerrado": self.enviada(periodo_estado="CERRADO"),
            "versión sin enmienda autorizada": self.enviada(version_de="m0", enmienda_estado="DESCARTADA"),
            "V4 otro verificador, pero actor": self.enviada(verificador=OTRO, actores=[OTRO, S]),
            "ya actuó en la cadena": self.enviada(actores_cadena=[OTRO, S]),
            "V5 fue técnico de la finca y período": self.enviada(tecnicos=[OTRO, S]),
            "propietario": self.enviada(propietario=S),
        }
        for nombre, m in casos.items():
            with self.subTest(caso=nombre):
                self.assertDecision(decidir_mfa("medicion:aprobar", "VF", m, mfa_edad=10), NIEGA, "C2")

    def test_v4_medicion_de_otro_verificador_es_404(self):
        self.assertDecision(decidir_mfa("medicion:aprobar", "VF", self.enviada(verificador=OTRO), mfa_edad=10),
                            FUERA_DE_ALCANCE, "C2")

    def test_version_con_enmienda_autorizada(self):
        version = self.enviada(version_de="m0", enmienda_estado="AUTORIZADA", periodo_estado="CERRADO")
        self.assertDecision(decidir_mfa("medicion:aprobar", "VF", version, mfa_edad=10), CONCEDE)

    def test_no_reta_si_ademas_falla_otra_cosa(self):
        self.assertDecision(decidir_mfa("medicion:aprobar", "VF", self.enviada(propietario=S)), NIEGA, "C2")


class C11C12Tests(Base):
    def test_c11_solicitar(self):
        aprobada = medicion(estado="APROBADA", ultima_aprobada="m1")
        self.assertDecision(decidir("enmienda:solicitar", "TC", aprobada), CONCEDE, "C11")
        self.assertDecision(decidir("enmienda:solicitar", "TC", medicion(estado="DEVUELTA", periodo_estado="CERRADO")),
                            CONCEDE)
        negadas = [medicion(), aprobada | {"enmienda_en_curso": True}, aprobada | {"ultima_aprobada": "m2"},
                   medicion(version_de="m0", periodo_estado="CERRADO")]
        for m in negadas:
            with self.subTest(m=m):
                self.assertDecision(decidir("enmienda:solicitar", "TC", m), NIEGA, "C11")
        self.assertDecision(decidir("enmienda:solicitar", "TC", aprobada | {"autor_original": OTRO}),
                            FUERA_DE_ALCANCE, "C11")

    def test_c11_versión_aprobada_mas_reciente(self):
        version = medicion(id="m2", version_de="m1", estado="APROBADA", ultima_aprobada="m2")
        self.assertDecision(decidir("enmienda:solicitar", "TC", version), CONCEDE)

    def test_c12_leer(self):
        e = {"tipo": "enmienda", "id": 1, "solicitante": S}
        self.assertDecision(decidir("enmienda:leer", "TC", e), CONCEDE, "C12")
        self.assertDecision(decidir("enmienda:leer", "TC", {**e, "solicitante": OTRO}), FUERA_DE_ALCANCE)


class C3Tests(Base):
    def enmienda(self, **cambios):
        base = {"tipo": "enmienda", "id": 1, "estado": "SOLICITADA", "solicitante": OTRO, "m0_estado": "APROBADA",
                "periodo_estado": "ABIERTO", "actores_m0": [OTRO], "propietario": 50, "justificacion_presente": True}
        return {**base, **cambios}

    def test_autorizar_y_rechazar(self):
        for accion in ("enmienda:autorizar", "enmienda:rechazar"):
            with self.subTest(accion=accion):
                self.assertDecision(decidir_mfa(accion, "GC", self.enmienda(), mfa_edad=5), CONCEDE, "C3")
                self.assertDecision(decidir_mfa(accion, "GC", self.enmienda()), "REAUTENTICAR", "C3")
        self.assertDecision(decidir_mfa("enmienda:autorizar", "GC", self.enmienda(m0_estado="DEVUELTA",
                                                                                    periodo_estado="CERRADO"),
                                        mfa_edad=5), CONCEDE)

    def test_negaciones(self):
        casos = [self.enmienda(estado="AUTORIZADA"), self.enmienda(m0_estado="ENVIADA"), self.enmienda(solicitante=S),
                 self.enmienda(actores_m0=[OTRO, S]),  # V7: el GC sincronizó la medición
                 self.enmienda(propietario=S), self.enmienda(justificacion_presente=False)]
        for e in casos:
            with self.subTest(e=e):
                self.assertDecision(decidir_mfa("enmienda:autorizar", "GC", e, mfa_edad=5), NIEGA, "C3")


class C16Tests(Base):
    def tecnico(self, estado="EN_BAJA"):
        return {"tipo": "usuario", "id": 30, "estado": estado, "roles": ["TC"]}

    def test_sincronizacion_final_vacia(self):
        self.assertDecision(decidir("medicion:sincronizar", "GC", None, {"tecnico": self.tecnico()}), CONCEDE, "C16")
        for estado in ("ACTIVA", "INACTIVA", "PENDIENTE"):
            with self.subTest(estado=estado):
                self.assertDecision(decidir("medicion:sincronizar", "GC", None, {"tecnico": self.tecnico(estado)}),
                                    NIEGA, "C16")
        self.assertDecision(decidir("medicion:sincronizar", "AC", None, {"tecnico": self.tecnico()}), NIEGA, "RG1")
        self.assertDecision(decidir("medicion:sincronizar", "GC"), NIEGA, "RG5")

    def test_con_registro(self):
        registro = {"firma_valida": True, "dispositivo_responsable": 30, "parcela_en_paquete": True,
                    "periodo_estado": "ABIERTO"}
        ctx = {"tecnico": self.tecnico(), "registro": registro}
        self.assertDecision(decidir("medicion:sincronizar", "GC", None, ctx), CONCEDE)
        for cambio in ({"firma_valida": False}, {"firma_valida": "si"}, {"dispositivo_responsable": 31},
                       {"parcela_en_paquete": False}, {"periodo_estado": "EN_VERIFICACION"}):
            with self.subTest(cambio=cambio):
                malo = {**ctx, "registro": {**registro, **cambio}}
                self.assertDecision(decidir("medicion:sincronizar", "GC", None, malo), NIEGA, "C16")
        self.assertDecision(decidir("medicion:sincronizar", "GC", None, {**ctx, "registro": {"firma_valida": True}}),
                            NIEGA, "RG5")
