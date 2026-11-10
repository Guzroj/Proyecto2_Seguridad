# Verdeazul · Plataforma de certificación de carbono

CE-1115 Seguridad de la Información · Proyecto Parte 2 · Grupo 05

Implementación del diseño de la Parte 1 (matriz de control de acceso, HBAC, separación de
responsabilidades) en Django + PostgreSQL, desplegado en Google Cloud Run con base en Neon.

---

## 1. Cómo correrlo en local

Requisitos: **Docker Desktop**. No hace falta instalar Python ni Postgres en la máquina
(en Windows, App Control bloquea la DLL de `psycopg`; dentro de Docker no hay problema).

```bash
# 1. Variables de entorno (una sola vez). .env está en .gitignore: nunca se versiona.
cp .env.example .env
#    Completar SECRET_KEY y POSTGRES_PASSWORD con valores aleatorios, p. ej.:
#    python -c "import secrets; print(secrets.token_urlsafe(50))"

# 2. Levantar Postgres 17 + Django (python 3.12, usuario no root)
docker compose up -d --build

# 3. Crear las tablas
docker compose exec web python manage.py migrate

# 4. Abrir http://127.0.0.1:8000
```

Comandos útiles:

| Para | Comando |
|---|---|
| Cualquier comando de Django | `docker compose exec web python manage.py <comando>` |
| Consola de Django | `docker compose exec web python manage.py shell` |
| Consola SQL | `docker compose exec db psql -U verdeazul -d verdeazul` |
| Correr pruebas | `docker compose exec web python manage.py test` |
| Ver logs (eventos provisionales de bitácora) | `docker compose logs -f web` |
| Apagar | `docker compose down` |
| Borrar la base local y empezar de cero | `docker compose down -v` |

El código se monta en vivo en el contenedor: al guardar un archivo, `runserver` se recarga.

### Producción (Cloud Run + Neon)

La misma imagen (`Dockerfile`) corre en Cloud Run con `gunicorn` en `$PORT`. Las variables
`SECRET_KEY`, `DATABASE_URL` (cadena de Neon con `sslmode=require`), `ALLOWED_HOSTS`
(`*.run.app` del servicio) y `CSRF_TRUSTED_ORIGINS` se inyectan desde **Secret Manager**.
Con `DEBUG=false` (por defecto) Django exige HTTPS, HSTS, cookies `Secure` y TLS hacia la base.
Las migraciones se corren contra Neon con `python manage.py migrate`.
*(Persona 1 completa esta sección con el pipeline y las etiquetas de versión publicadas.)*

---

## 2. Estructura

```
verdeazul/        configuración (settings lee TODO de variables de entorno)
nucleo/           piezas compartidas: errores, vocabulario de acciones, ganchos, decorador transicion
cuentas/          Usuario (O7a) — mínimo; Persona 3 agrega roles, estado, MFA
catalogo/         Finca, Parcela, ParcelaVersion (O1a), DatosPropietario (O1b) + services.py
mediciones/       Periodo (O2), Asignacion (O3), Medicion + MedicionActor (O4), Enmienda (O5)
  services.py     ÚNICA vía para cambiar estados (ninguna vista hace .save() directo)
  selectores.py   funciones de atributos de la matriz: actores(m), tecnicos(f,p), ...
  carbono.py      cálculo de toneladas de CO2
```

---

## 3. Contrato entre equipos

### 3.1 Cómo se usa un servicio

Toda operación sigue: **bloquear el objeto (FOR UPDATE) → `autorizar()` → invariantes de
dominio → cambio + actor + evento**, dentro de una transacción.

```python
from mediciones import services
services.aprobar_medicion(request.user, medicion_id)      # acepta objeto o pk
```

Excepciones (`nucleo/errores.py`) y respuesta HTTP sugerida:

| Excepción | Quién la lanza | HTTP |
|---|---|---|
| `AccesoDenegado` | motor de autorización (P3) | 403 |
| `ReautenticacionRequerida` | motor (P3): solo falta MFA reciente | 401 + reto |
| `FueraDeAlcance` | motor (P3) o servicio si el objeto no existe | 404 (RG3) |
| `TransicionInvalida` | servicio: el estado no lo permite (H4, C13...) | 409 |
| `SeparacionDeFunciones` | servicio: participó o es dueño (C2, C3, C19) | 403 |
| `SinAsignacion` | servicio: técnico sin asignación vigente (C1) | 403 |
| `DatoInvalido` | servicio: fuera de lista blanca | 400 |
| `SinVerificadorDisponible` | servicio: no queda VF elegible | 409 |

Toda negación se registra automáticamente en la bitácora con `resultado="DENEGADO"` (RG4).

### 3.2 Ganchos (`nucleo/ganchos.py`)

Se conectan en `settings.VERDEAZUL_GANCHOS` con la ruta de la función; no hay que tocar los servicios.

| Gancho | Dueño | Firma | Qué hace |
|---|---|---|---|
| `autorizar` | **Persona 3** | `(usuario, accion, recurso, contexto) -> None` | Rol vigente, alcance ABAC, MFA, H1–H3. Lanza `AccesoDenegado`/`FueraDeAlcance`/`ReautenticacionRequerida` si niega. |
| `usuarios_con_rol` | **Persona 3** | `(rol: str) -> QuerySet[Usuario]` | Usuarios activos con el rol vigente (`"TC"`, `"VF"`, ...). |
| `registrar_evento` | **Persona 4** | `(*, actor, accion, recurso_tipo, recurso_id, resultado, antes, despues, detalle)` | Bitácora encadenada. Se llama dentro de la transacción. |
| `on_medicion_aprobada` | **Persona 4** | `(medicion) -> None` | Emitir y firmar el certificado. Corre en la transacción de aprobación con la medición bloqueada; si lanza excepción, la aprobación se revierte. Si `medicion.version_de` no es `None`, aplicar P2. |

Implementaciones provisionales mientras tanto: `autorizar` **niega todo** (fallo cerrado), salvo
con `DEBUG=true` en local; `usuarios_con_rol` usa Groups de Django con el nombre del rol;
los eventos se escriben en el log.

### 3.3 Vocabulario de acciones (`nucleo/acciones.py`)

`finca:crear|modificar|baja` · `parcela:crear|modificar_geometria|baja` · `propietario:registrar_datos` ·
`periodo:abrir|pasar_verificacion|cerrar` · `asignacion:crear|terminar` ·
`medicion:crear|modificar|enviar|aprobar|devolver` · `enmienda:solicitar|autorizar|rechazar`

Cada constante indica en un comentario su celda de la matriz (p. ej. `MEDICION_APROBAR` → VF·O4 AP, C2).

### 3.4 Funciones de atributos (`mediciones/selectores.py`)

| Función | Notación de la matriz |
|---|---|
| `actores(m)` / `actores_cadena(m)` | actores(m); la cadena incluye original y versiones |
| `tecnicos(finca_id, periodo_id)` | tecnicos(f,p) — incluye asignaciones terminadas |
| `asignacion_vigente(usuario_id, finca_id, periodo_id)` | asig(s,f,p) |
| `fincas_asignadas(usuario_id)` | alcance de C6 |
| `propietario_id(m)` | f.propietario |
| `raiz(m)`, `ultima_aprobada(m)`, `enmienda_en_curso(m)` | soporte de C11/C3 |

### 3.5 Modelos y campos clave (para certificados y exportaciones)

**`Medicion`** (`id` UUID): `parcela`, `parcela_version` (geometría usada), `periodo`, `autor`,
`estado` (`BORRADOR`/`ENVIADA`/`APROBADA`/`DEVUELTA`), `verificador`, `version_de` (original si es
versión enmendada), `numero_arboles`, `dap_promedio_cm`, `altura_promedio_m`, **`toneladas_co2`**,
`aprobada_en`, `enmienda_origen` (inverso, en versiones). Finca y propietario: `m.parcela.finca.propietario`.

**`Enmienda`**: `medicion_original` (la que se enmienda), `solicitante`, `justificacion`, valores
propuestos, `estado` (`SOLICITADA`/`AUTORIZADA`/`RECHAZADA`/`APLICADA`/`DESCARTADA`), `autorizador`, `version`.

**`Periodo`**: `ABIERTO` → `EN_VERIFICACION` → `CERRADO`. **`Asignacion`**: vigente si `vigente_hasta IS NULL`.

### 3.6 Lo que la base de datos rechaza sola (aunque alguien se salte el código)

- Segunda medición **original aprobada** para la misma parcela y período (V6).
- Verificador igual al autor; enviada/aprobada sin verificador; aprobada sin toneladas; devuelta sin motivo.
- Rangos físicos fuera de lo plausible.
- Enmienda sin justificación, autorizada por su solicitante, o dos en curso para la misma medición.
- Dos versiones de geometría vigentes para la misma parcela; dos asignaciones vigentes iguales.
- Ningún borrado en cascada (`PROTECT`): las bajas son cambios de estado.

---

## 4. Decisiones de implementación y desviaciones respecto a la Parte 1

*(Insumo para la sección "Desviaciones" del informe.)*

| # | Decisión | Por qué | Implicación de seguridad |
|---|---|---|---|
| D1 | **Cálculo de CO2**: ecuación pantropical de Chave et al. (2014), `AGB = 0.0673·(ρ·D²·H)^0.976` kg por árbol, ρ = 0.6 g/cm³; carbono = 47 % (IPCC 2006); CO2 = C·44/12. Árbol promedio × número de árboles. | La Parte 1 solo dice "el servidor recalcula el carbono con los datos crudos", sin fórmula. | El cliente nunca envía toneladas; el servidor las recalcula al crear, modificar y **otra vez al aprobar**. Simplificación: árbol promedio y stock en pie, no incremento. |
| D2 | Geometría como **GeoJSON validado** (lista blanca) en vez de **PostGIS**. | PostGIS exige GDAL en la imagen. | **PENDIENTE**: no se valida traslape entre parcelas (parte de C20). Se puede activar PostGIS después (Neon lo soporta). |
| D3 | Sin PWA sin conexión ni paquetes de trabajo firmados. | Alcance de la Parte 2 (el enunciado permite recortar funcionalidad accesoria). | C13 `TV` no verifica paquetes vigentes; C16 (sincronización final) queda sin flujo. Riesgo R6 no aplica al no haber captura offline. |
| D4 | Verificador **nuevo** en cada envío. | Consecuencia de C2: quien devolvió queda en actores(m) y no puede aprobar. | Ojos frescos tras cada devolución; exige al menos 3 VF activos para un ciclo enviar→devolver→reenviar→enmendar. |
| D5 | On-premise → Cloud Run + Neon; OpenBao → Secret Manager. | Exigencia del enunciado de la Parte 2. | *(Persona 1/4 completan.)* |
| D6 | Django corre dentro de Docker también en desarrollo; Python 3.12. | App Control de Windows bloquea `psycopg`; misma versión que producción. | Paridad desarrollo–producción. |

---

## 5. Avance de Persona 2

- [x] Esqueleto Django, settings sin secretos, Docker + compose
- [x] Modelos y restricciones (catálogo, períodos, asignaciones, mediciones, actores, enmiendas)
- [x] Servicios con todas las transiciones + ganchos + selectores + cálculo de CO2
- [ ] Disparadores de Postgres (H4, `MedicionActor` solo inserción, versiones de geometría inmutables)
- [ ] Comando `seed` con un usuario por rol (credenciales de prueba)
- [ ] Pruebas automatizadas de los casos V1–V18 que tocan el dominio
- [ ] PostGIS para traslape de parcelas (D2)
