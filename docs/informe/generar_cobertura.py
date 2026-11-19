"""
Genera cobertura_filas.tex (filas de la Tabla de cobertura del informe) a partir de
docs/cobertura-matriz.md, que a su vez genera `manage.py tabla_cobertura` y valida
autorizacion/test_cobertura_tabla.py. Nada se transcribe a mano.

Uso:  python generar_cobertura.py ../cobertura-matriz.md cobertura_filas.tex

- Una fila por cada celda con permiso.
- Las celdas denegadas ("—") de cada rol se agrupan en una fila que enumera sus números.
- Las 13 celdas del AE (todas L [C5]; X [C5, H3]) comparten una fila.
- "Dónde": módulos PEP/servicio (sin autorizacion/reglas.py y listas.py, comunes a todas)
  y condiciones de autorizacion/condiciones.py; "HBAC" = autorizacion/hbac.py.
- "Prueba": la prueba propia más específica (prefiere la que nombra la condición de la celda).
  Prefijo omitido: autorizacion/ (test_condiciones, test_integracion_*, test_listas, test_hbac).
"""
import re
import sys
from collections import OrderedDict

md, salida = sys.argv[1], sys.argv[2]
filas = []
for linea in open(md, encoding="utf-8"):
    if not re.match(r"^\| \d+ \|", linea):
        continue
    c = [x.strip() for x in linea.strip().strip("|").split("|")]
    num, rol, obj, celda, acciones, donde, pruebas = c[:7]
    filas.append(dict(n=int(num), rol=rol, obj=obj.split(" · ")[0], celda=celda, donde=donde, pruebas=pruebas))
assert len(filas) == 78, len(filas)
GENERICAS = ("CeldaPorCeldaTests", "MatrizTests", "test_celdas_sin_condicion")


def items(campo):
    return [x.replace("`", "") for x in campo.split("<br>") if x.strip()]


def celda_tex(celda):
    return celda.replace("‡", r"\ddag{}").replace("—", "---")


def donde(f):
    conds, mods = [], OrderedDict()
    for p in items(f["donde"]):
        if p.startswith(("autorizacion/reglas.py", "autorizacion/listas.py")):
            continue
        m = re.match(r"autorizacion/condiciones\.py:(\w+)", p)
        if m:
            conds.append(m.group(1).upper())
        elif p.startswith("autorizacion/hbac.py"):
            conds.append("HBAC")
        else:
            mods[p.split(":")[0].replace(".py", "")] = 1
    # Con servicios, las pantallas solo los invocan: se muestran los servicios.
    servicios = [m for m in mods if "pantallas" not in m]
    elegidos = servicios or list(mods)
    texto = "; ".join(r"\path{%s}" % m for m in elegidos) or r"\path{reglas:decidir}"
    cs = list(OrderedDict.fromkeys(conds))
    return texto + (" · " + ", ".join(cs) if cs else "")


def prueba(f):
    conds = set(re.findall(r"C\d+", f["celda"]))
    hconds = set(re.findall(r"H\d+", f["celda"]))

    def rango(p):
        nombre = p.split("::")[-1].lower()
        if any(re.search(c.lower() + r"(?!\d)", nombre) for c in conds) or ("C20" in conds and "traslape" in p):
            return 0
        if any(re.search(h.lower() + r"(?!\d)", nombre) for h in hconds):
            return 1
        return 2

    candidatas = [p for p in items(f["pruebas"]) if not any(g in p for g in GENERICAS)]
    if not candidatas:
        return r"\path{test_cobertura::CeldaPorCeldaTests}"
    p = sorted(candidatas, key=rango)[0]
    arch, _, resto = p.partition("::")
    arch = arch.replace(".py", "").replace("autorizacion/", "")
    return r"\path{%s::%s}" % (arch, resto.split(".")[0])


lineas = []
for rol in ["TC", "VF", "GC", "AC", "AE", "PF"]:
    del_rol = [x for x in filas if x["rol"] == rol]
    if rol == "AE":
        assert all(x["celda"] == "L [C5]; X [C5, H3]" for x in del_rol)
        lineas.append(
            f"{del_rol[0]['n']}--{del_rol[-1]['n']} & AE & las 13: " + ", ".join(x["obj"] for x in del_rol)
            + r" & L [C5]; X [C5, H3] en cada una & \path{autorizacion/cliente}; \path{exportaciones/servicios};"
            r" pantalla de cada objeto · C5, HBAC & \path{test_condiciones::C5Tests};"
            r" \path{exportaciones/test_exportaciones::ExportarTests}; \path{test_hbac::H3Tests}\\")
        lineas.append(r"\midrule")
        continue
    for f in [x for x in del_rol if x["celda"] != "—"]:
        lineas.append(f"{f['n']} & {rol} & {f['obj']} & {celda_tex(f['celda'])} & {donde(f)} & {prueba(f)}\\\\")
    negadas = [x for x in del_rol if x["celda"] == "—"]
    if negadas:
        lineas.append(
            ", ".join(str(x["n"]) for x in negadas) + f" & {rol} & " + ", ".join(x["obj"] for x in negadas)
            + r" & --- (denegada) & \path{reglas:decidir} (RG1) y RLS & \path{test_cobertura::CeldaPorCeldaTests}\\")
    lineas.append(r"\midrule")
lineas.pop()
open(salida, "w", encoding="utf-8").write("\n".join(lineas) + "\n")
print(f"{len(filas)} celdas: {sum(1 for x in filas if x['celda'] != '—')} con permiso, "
      f"{sum(1 for x in filas if x['celda'] == '—')} denegadas; {len(lineas)} líneas")
