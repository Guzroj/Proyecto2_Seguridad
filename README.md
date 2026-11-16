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
| Ver logs (eventos de bitácora) | `docker compose logs -f web` |
| Apagar | `docker compose down` |
| Borrar la base local y empezar de cero | `docker compose down -v` |

El código se monta en vivo en el contenedor: al guardar un archivo, `runserver` se recarga.

### Producción (Cloud Run + Neon)

La misma imagen (`Dockerfile`) corre en Cloud Run con `gunicorn` en `$PORT`. Las variables
`SECRET_KEY`, `DATABASE_URL` (cadena de Neon con `sslmode=require`), `ALLOWED_HOSTS`
(`*.run.app` del servicio) y `CSRF_TRUSTED_ORIGINS` se inyectan desde **Secret Manager**.
Con `DEBUG=false` (por defecto) Django exige HTTPS, HSTS, cookies `Secure` y TLS hacia la base.
Las migraciones se corren contra Neon con `python manage.py migrate`.

#### Arquitectura de despliegue

Dos servicios independientes en la región **`us-east1`**, cada uno con su propia
imagen en Artifact Registry (repo Docker `verdeazul`):

| Servicio Cloud Run | Imagen | Expuesto | Función |
|---|---|---|---|
| `verdeazul` | `app` | Público (`*.run.app`) | Django + gunicorn: UI, sesiones, CSRF. |
| `verdeazul-autorizador` | `autorizador` | Privado (sin `allUsers`) | Motor de autorización, invocado servicio-a-servicio. |

`verdeazul` llama a `verdeazul-autorizador` con su *service account* y el token de identidad
de Cloud Run (audiencia = URL del autorizador), así que el autorizador solo acepta tráfico
autenticado. La URL privada llega a Django en la variable `AUTORIZADOR_URL`.

#### Pipeline de GitHub Actions

`.github/workflows/deploy.yml` se autentica con **Workload Identity Federation** (WIF, sin
claves JSON de service account de larga vida). En cada push a `main`:

1. Construye las dos imágenes con **tags explícitos**: `v1.0.0` y `sha-<hash corto>`.
2. Hace push a Artifact Registry:
   `us-east1-docker.pkg.dev/<proyecto>/verdeazul/app` y `.../verdeazul/autorizador`.
3. Despliega ambos servicios en Cloud Run por etiqueta de imagen inmutable.
4. El job `verdeazul-migrate` ejecuta `python manage.py migrate` contra Neon usando el
   secreto del rol dueño.

Nunca se publica solo `latest`: cada despliegue queda atado a una etiqueta de versión y a un
commit, lo que permite volver a una imagen anterior sin ambigüedad.

#### Secretos y variables (Secret Manager)

| Secreto / variable | Contenido | Consumido por |
|---|---|---|
| `verdeazul-secret-key` | `SECRET_KEY` de Django | `verdeazul` |
| `verdeazul-db-url` | `DATABASE_URL` de Neon con **rol runtime no-dueño** (`app_runtime`) y `sslmode=require` | `verdeazul` |
| `verdeazul-migrate-db-url` | `DATABASE_URL` con el **rol dueño** | job `verdeazul-migrate` |
| `verdeazul-allowed-hosts` | `ALLOWED_HOSTS` (`*.run.app` del servicio) | `verdeazul` |
| `verdeazul-csrf-origins` | `CSRF_TRUSTED_ORIGINS` (orígenes HTTPS) | `verdeazul` |
| `AUTORIZADOR_URL` | URL privada del servicio autorizador | `verdeazul` |

Los valores se montan como variables de entorno; el runtime nunca recibe credenciales del
rol dueño.

#### Dos roles de base de datos (mínimo privilegio + RLS)

| Rol | Uso | Privilegios |
|---|---|---|
| dueño (rol por defecto de Neon, p. ej. `neondb_owner`) | Solo migraciones (job `verdeazul-migrate`). | DDL: crea y altera el esquema. |
| `app_runtime` | Runtime de `verdeazul` y del autorizador. | DML sobre las tablas; **no** es dueño ni altera el esquema. |

`app_runtime` se crea **por SQL con el rol dueño**, no desde la consola/API de Neon: en este
proyecto esas vías crean el rol con `BYPASSRLS`, `CREATEDB`, `CREATEROLE` y membresía en
`neon_superuser`, lo que anula RLS. La contraseña vive solo en Secret Manager.

```sql
CREATE ROLE app_runtime LOGIN PASSWORD '<aleatoria>'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
```

Antes de continuar, verificar que quede `rolsuper=f`, `rolbypassrls=f`, `rolcreatedb=f`,
`rolcreaterole=f` y sin membresías.

`db/roles_rls.sql` otorga los privilegios DML a `app_runtime` y activa **RLS con
`FORCE ROW LEVEL SECURITY`**, de modo que las políticas se aplican incluso al dueño de las
tablas y ningún camino del código puede saltarlas. `db/grant_after_migrate.sql` vuelve a otorgar los privilegios a `app_runtime`
después de cada `migrate`, porque las tablas nuevas no heredan permisos.

> **Neon Auth:** no habilitarlo si no se usa. Crea los roles `authenticated`, `anonymous` y
> `authenticator`, y les concede DML por defecto sobre `public` (incluidas tablas de Django sin
> RLS, como `django_session`). Si se habilitó por error, revocar esos privilegios y desactivarlo.

#### Service accounts (mínimo privilegio)

| Service account | Usada por | Roles |
|---|---|---|
| `verdeazul-app` | servicio `verdeazul` | `secretAccessor` en los 4 secretos de runtime; `run.invoker` sobre el autorizador. |
| `verdeazul-autorizador` | servicio `verdeazul-autorizador` | Sin roles de proyecto ni acceso a secretos. |
| `verdeazul-migrate` | Cloud Run Job `verdeazul-migrate` | `secretAccessor` solo en `verdeazul-migrate-db-url` y `verdeazul-secret-key`. |
| `verdeazul-deploy` | GitHub Actions (WIF) | `run.admin`, `artifactregistry.writer`, `browser`, `secretAccessor` en los 5 secretos y `iam.serviceAccountUser` sobre las 3 SA de runtime. |

El autorizador se despliega con `--no-allow-unauthenticated` y **sin IAP**: Cloud Run IAM valida
el token OIDC que `verdeazul` pide al metadata server con `audience = AUTORIZADOR_URL`.

#### Dependabot

`.github/dependabot.yml` mantiene actualizadas las dependencias (pip y Docker) con
PRs automáticos, reduciendo la ventana de exposición a vulnerabilidades conocidas.

#### Despliegue de validación actual

| Recurso | Valor |
|---|---|
| App pública | `https://verdeazul-444812071990.us-east1.run.app` |
| Autorizador privado | `https://verdeazul-autorizador-444812071990.us-east1.run.app` |
| Proyecto / región | `project-4d85c806-d471-4964-b73` / `us-east1` |
| Imagen desplegada | `manual-202611121557` (validación manual) |

El autorizador quedó con `roles/run.invoker` solo para `verdeazul-app`. El path `/healthz`
de FastAPI queda sombreado por el frontend de Cloud Run; para probar el motor usar
`POST /autorizar` (403 fail-closed) o `GET /usuarios_con_rol/VF` (200 con lista vacía).

#### Prerrequisitos (setup manual, una sola vez)

1. **Neon**: crear el proyecto (PostgreSQL 18, región `aws-us-east-1`) y la base `neondb`;
   anotar las cadenas **directas** con `sslmode=require` (dueño para migrar, `app_runtime` para runtime).
2. **Artifact Registry**: crear el repo Docker `verdeazul` en `us-east1` (misma región que Cloud Run).
3. **Cloud Run**: crear los dos servicios con las SA dedicadas (`verdeazul-app` y
   `verdeazul-autorizador`); el job de migraciones usa `verdeazul-migrate`. Dar al autorizador
   solo el permiso de invocación a `verdeazul-app`.
4. **Secret Manager**: cargar los cinco secretos de la tabla anterior.
5. **Base de datos**: crear `app_runtime` por SQL (ver *Dos roles*), ejecutar `db/roles_rls.sql`
   con el rol dueño y `db/grant_after_migrate.sql` después de `migrate`.
6. **GitHub**: registrar el pool/proveedor de WIF (`github` / `github-oidc`, condición
   `assertion.repository == 'Guzroj/Proyecto2_Seguridad'`). Las variables del workflow
   (proyecto, región, provider y service account) son opcionales: trae fallback para este
   proyecto. Sin claves JSON de larga vida.

---

## 2. Estructura

```
verdeazul/        configuración (settings lee TODO de variables de entorno)
nucleo/           piezas compartidas: errores, vocabulario de acciones, ganchos, decorador transicion
cuentas/          Usuario (O7a) — autenticación básica
catalogo/         Finca, Parcela, ParcelaVersion (O1a), DatosPropietario (O1b) + services.py
mediciones/       Periodo (O2), Asignacion (O3), Medicion + MedicionActor (O4), Enmienda (O5)
  services.py     ÚNICA vía para cambiar estados (ninguna vista hace .save() directo)
  selectores.py   funciones de atributos de la matriz: actores(m), tecnicos(f,p), ...
  carbono.py      cálculo de toneladas de CO2
```

---

## 3. Contrato de integración

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
| `AccesoDenegado` | motor de autorización | 403 |
| `ReautenticacionRequerida` | motor de autorización: solo falta MFA reciente | 401 + reto |
| `FueraDeAlcance` | motor de autorización o servicio si el objeto no existe | 404 (RG3) |
| `TransicionInvalida` | servicio: el estado no lo permite (H4, C13...) | 409 |
| `SeparacionDeFunciones` | servicio: participó o es dueño (C2, C3, C19) | 403 |
| `SinAsignacion` | servicio: técnico sin asignación vigente (C1) | 403 |
| `DatoInvalido` | servicio: fuera de lista blanca | 400 |
| `SinVerificadorDisponible` | servicio: no queda VF elegible | 409 |

Toda negación se registra automáticamente en la bitácora con `resultado="DENEGADO"` (RG4).

### 3.2 Ganchos (`nucleo/ganchos.py`)

Se conectan en `settings.VERDEAZUL_GANCHOS` con la ruta de la función; no hay que tocar los servicios.

| Gancho | Firma | Qué hace |
|---|---|---|
| `autorizar` | `(usuario, accion, recurso, contexto) -> None` | Rol vigente, alcance ABAC, MFA, H1–H3. Lanza `AccesoDenegado`/`FueraDeAlcance`/`ReautenticacionRequerida` si niega. |
| `usuarios_con_rol` | `(rol: str) -> QuerySet[Usuario]` | Usuarios activos con el rol vigente (`"TC"`, `"VF"`, ...). |
| `registrar_evento` | `(*, actor, accion, recurso_tipo, recurso_id, resultado, antes, despues, detalle)` | Bitácora encadenada. Se llama dentro de la transacción. |
| `on_medicion_aprobada` | `(medicion) -> None` | Emitir y firmar el certificado. Corre en la transacción de aprobación con la medición bloqueada; si lanza excepción, la aprobación se revierte. Si `medicion.version_de` no es `None`, tratar como versión enmendada. |

Comportamiento actual: `autorizar` **niega todo** (fallo cerrado), salvo con `DEBUG=true` en
local; `usuarios_con_rol` usa Groups de Django con el nombre del rol; los eventos se escriben en el log.

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

| # | Decisión | Por qué | Implicación de seguridad |
|---|---|---|---|
| D1 | **Cálculo de CO2**: ecuación pantropical de Chave et al. (2014), `AGB = 0.0673·(ρ·D²·H)^0.976` kg por árbol, ρ = 0.6 g/cm³; carbono = 47 % (IPCC 2006); CO2 = C·44/12. Árbol promedio × número de árboles. | La Parte 1 solo dice "el servidor recalcula el carbono con los datos crudos", sin fórmula. | El cliente nunca envía toneladas; el servidor las recalcula al crear, modificar y **otra vez al aprobar**. Simplificación: árbol promedio y stock en pie, no incremento. |
| D2 | Geometría como **GeoJSON validado** (lista blanca) en vez de **PostGIS**. | PostGIS exige GDAL en la imagen. | Limitación actual: no se valida traslape entre parcelas (parte de C20). |
| D3 | Sin PWA sin conexión ni paquetes de trabajo firmados. | La captura offline y los paquetes firmados no forman parte de la implementación. | C13 `TV` no verifica paquetes vigentes; C16 (sincronización final) queda sin flujo. Riesgo R6 no aplica al no haber captura offline. |
| D4 | Verificador **nuevo** en cada envío. | Consecuencia de C2: quien devolvió queda en actores(m) y no puede aprobar. | Ojos frescos tras cada devolución; exige al menos 3 VF activos para un ciclo enviar→devolver→reenviar→enmendar. |
| D5 | On-premise → Cloud Run + Neon; OpenBao → Secret Manager. | Migración a servicios gestionados. | Secretos fuera de la imagen y del repo; TLS terminado por Cloud Run; identidad de servicio vía WIF. |
| D6 | Django corre dentro de Docker también en desarrollo; Python 3.12. | App Control de Windows bloquea `psycopg`; misma versión que producción. | Paridad desarrollo–producción. |

## 5. Cuentas, autenticación y motor de autorización (Persona 3)

Esta parte implementa la matriz de control de acceso completa (78 celdas, C1–C22, H1–H4),
las cuentas y sus roles, el ingreso con segundo factor y Row Level Security en todas las tablas.
La tabla de cobertura permiso por permiso está en [`docs/cobertura-matriz.md`](docs/cobertura-matriz.md).

### 5.1 Cómo se usa en local

`docker compose up -d` levanta, además de `db` y `web`, el servicio **`autorizador`** (el motor) en
`127.0.0.1:8080`; `web` espera a que esté sano. Si el motor no responde, **se niega todo** (RG5).

**Cuentas de prueba.** `crear_usuarios_prueba` crea, ACTIVAS y con la misma contraseña:
`prueba_tc`, `prueba_tc2` (TC) · `prueba_vf`, `prueba_vf2`, `prueba_vf3` (VF) · `prueba_gc`,
`prueba_gc2` (GC) · `prueba_ac` (AC) · `prueba_ae` (AE, con ventana de 30 días desde hoy aprobada
por `prueba_gc`) · `prueba_pf` (PF). Hay varias de VF y GC porque C2, C3 y C7 exigen personas
distintas. **`prueba_pf` no tiene segundo factor**; todas las demás comparten el mismo secreto TOTP:
se agrega una sola vez en la app de autenticación con "Ingresar clave de configuración" (tipo
basada en el tiempo). El comando imprime la URI del TOTP y los códigos de recuperación una sola vez;
las cuentas que ya existen no se tocan. La contraseña y el secreto se leen del entorno, nunca del
código ni de la línea de comandos.

Bash (Linux, macOS, WSL):

```bash
read -rs VZ_PRUEBA_CLAVE && export VZ_PRUEBA_CLAVE
export VZ_PRUEBA_TOTP=$(python3 -c "import base64,secrets;print(base64.b32encode(secrets.token_bytes(20)).decode())")
docker compose exec -e VZ_PRUEBA_CLAVE -e VZ_PRUEBA_TOTP web python manage.py crear_usuarios_prueba
unset VZ_PRUEBA_CLAVE VZ_PRUEBA_TOTP
```

PowerShell (Docker Desktop en Windows; el secreto TOTP se genera dentro del contenedor):

```powershell
$clave = Read-Host "Contraseña de las cuentas de prueba (mínimo 12 caracteres)" -AsSecureString
$env:VZ_PRUEBA_CLAVE = [System.Net.NetworkCredential]::new("", $clave).Password
$env:VZ_PRUEBA_TOTP = (docker compose exec -T web python -c "import base64,secrets;print(base64.b32encode(secrets.token_bytes(20)).decode())").Trim()
docker compose exec -e VZ_PRUEBA_CLAVE -e VZ_PRUEBA_TOTP web python manage.py crear_usuarios_prueba
Remove-Item Env:VZ_PRUEBA_CLAVE, Env:VZ_PRUEBA_TOTP
```

Otros comandos:

```bash
# Tarea diaria (activa y da de baja las cuentas AE según su ventana)
docker compose exec web python manage.py tarea_diaria_cuentas

# Pruebas: Django y el motor en su propio contenedor (sin Django ni base)
docker compose exec web python manage.py test
docker compose exec autorizador python -m unittest

# Regenerar la tabla de cobertura (una prueba falla si no está al día)
docker compose exec web python manage.py tabla_cobertura
```

Pantallas (`/gestion/`, según el rol): cuentas, mi cuenta, roles y solicitudes de rol, ventanas de
auditoría, dispositivos de campo, restablecimientos del segundo factor y alertas. No hay registro
público: AC crea la cuenta y entrega un enlace de activación de un solo uso por otro canal.

### 5.2 Arquitectura del motor

- **Django es el PEP** (`autorizacion/pep.py`, `autorizacion/cliente.py`): en cada decisión lee de la
  base los atributos del sujeto (roles vigentes, estado, edad del segundo factor), del objeto y los
  contadores HBAC. Nunca envía datos personales, solo ids, estados y conjuntos de ids.
- **El autorizador es el PDP** (`autorizacion/app.py`, FastAPI, sin acceso a la base): decide con la
  matriz como datos (`autorizacion/matriz.py`) y las reglas (`reglas.py`, `condiciones.py`, `hbac.py`).
  En Cloud Run se despliega con `--no-allow-unauthenticated` y Django le envía un token OIDC.
- Regla: concede si algún rol vigente tiene la operación en la celda y cumple sus condiciones, y
  ninguna prohibición (H4, suspensión H1) lo veta. Resultado → excepción:
  `CONCEDE` → nada · `NIEGA` → `AccesoDenegado` (403) · `FUERA_DE_ALCANCE` → `FueraDeAlcance` (404) ·
  `REAUTENTICAR` → `ReautenticacionRequerida` (reto de segundo factor). Cualquier otra cosa niega.

### 5.3 Para el resto del equipo

Esto actualiza lo que dice 3.2 sobre el comportamiento actual de los ganchos:

| Pieza | Uso |
|---|---|
| `autorizar` | Conectado al motor real (`autorizacion.cliente.autorizar`) también en local. Solo se concede lo que la matriz permite; aprobar, devolver y autorizar enmiendas piden segundo factor reciente, así que desde la consola responden `ReautenticacionRequerida`. |
| `usuarios_con_rol` | `cuentas.roles.usuarios_con_rol`: cuentas ACTIVAS con el rol vigente. |
| Listas | `autorizacion.listas.listar(usuario, "fincas")` devuelve un QuerySet ya filtrado al alcance del sujeto (fincas, parcelas, propietarios, periodos, asignaciones, mediciones, enmiendas, cuentas, sesiones, ventanas, roles, solicitudes_rol, dispositivos, restablecimientos, alertas). |
| Lectura de un objeto | `ganchos.autorizar(usuario, "finca:leer", finca)`; fuera de alcance da `FueraDeAlcance` (404). |
| Exportaciones | Pasar `{"registros": N}` en el contexto de `autorizar(..., "<objeto>:exportar", ...)` (si falta, se niega) y poner en el archivo `cuentas.hbac.marca_de_agua(usuario)`. |
| Certificado (Persona 4) | `autorizacion.procesos.datos_propietario_para_certificado(medicion)` devuelve nombre e identificación del propietario solo al VF que aprueba esa medición; el VF no tiene acceso directo a O1b. |
| Errores en pantallas | `cuentas.errores.ErroresMiddleware` traduce las excepciones a 403 / 404 / 400 / 409. |
| Pruebas de otros módulos | Para usar el motor real sin levantarlo: `autorizacion.test_cliente.MotorFalso`. Para aislar un servicio: quitar el gancho `autorizar` y activar `AUTORIZACION_PROVISIONAL_PERMISIVA`. |

#### Cambios que afectan otras secciones del README

Esta parte cambió cosas que otras secciones describen; esas secciones no se editaron:

- **Sección 1, Despliegue de validación**: `GET /usuarios_con_rol/...` ya no existe (lo resuelve Django
  leyendo la base) y `POST /autorizar` devuelve la decisión en JSON (`resultado`, `regla`, `motivo`,
  `rol`, `roles`, `hbac`, `alertas`, `marca_agua`), no siempre 403. Una solicitud mal formada da 422.
- **Sección 2, Estructura**: existe el paquete `autorizacion/` (PEP: `pep.py`, `cliente.py`,
  `listas.py`, `procesos.py`; PDP: `app.py`, `matriz.py`, `reglas.py`, `condiciones.py`, `hbac.py`), y
  `cuentas/` ya no es solo autenticación básica: estados de cuenta, roles con vigencia, sesiones,
  segundo factor, ventanas de auditoría, dispositivos de campo, restablecimientos, alertas, RLS y
  pantallas de gestión.
- **Sección 3.3, Vocabulario de acciones**: ahora incluye lectura (`<objeto>:leer`), exportación
  (`<objeto>:exportar`) y listas (`<objeto>:listar`) de las 13 columnas, y las acciones de O7–O9
  (cuentas, roles, ventanas, dispositivos, segundo factor y alertas). La lista completa está en
  `nucleo/acciones.py`.
- **D3**: C16 ya tiene flujo, con la sincronización final vacía que cierra la baja de un técnico (P3-2).

### 5.4 Producción

| Variable | Valor |
|---|---|
| `AUTORIZADOR_URL` | URL del servicio `verdeazul-autorizador` (ya la resuelve `deploy.yml`). |
| `AUTORIZADOR_AUTENTICACION` | `oidc` (por defecto). `ninguna` solo se acepta con `DEBUG`. |
| `MFA_CLAVE_CIFRADO` | Secreto en Secret Manager para cifrar los secretos TOTP; si falta, se deriva de `SECRET_KEY`. |

- La tarea diaria `tarea_diaria_cuentas` debe correr como Cloud Run Job disparado por Cloud Scheduler
  (`5 0 * * *`, zona `America/Costa_Rica`).
- Las migraciones dan `GRANT EXECUTE` a `app_runtime` sobre `cuentas_id_para_login`,
  `cuentas_media_lecturas_rol` y `certificado_datos_propietario` si el rol ya existe; si se creó
  después, repetir esos tres `GRANT` como dueño.

### 5.5 Row Level Security

Todas las tablas tienen RLS menos `django_session` (se carga por su clave antes de saber quién es el
sujeto; el control está en el registro de sesiones de `cuentas`). El sujeto y la fase se fijan en cada
petición (`app.usuario_id`, `app.fase`) desde la sesión del servidor.

| Tablas | Lectura | Escritura |
|---|---|---|
| Cuentas, roles, sesiones, ventanas, dispositivos, factores, alertas | Según la matriz y el propio sujeto | Solo el rol de la celda, a su nombre; disparadores para lo que no puede cambiar |
| Catálogo, asignaciones, mediciones, actores, enmiendas | **Finca relacionada**: GC, AC y AE todo; los demás, las fincas de las que son dueños, técnicos, autores, verificadores o actores | Solo el rol de la celda, a su nombre (AC el catálogo, GC los periodos, TC y VF sus mediciones) |
| Datos personales del propietario (O1b) | El propietario, AC y AE | Solo AC |
| Correos de allauth y bitácora del admin | El propio sujeto (AC, todos los correos) | El propio sujeto |
| Metadatos de Django | Todos (RLS sin FORCE: `migrate` los escribe como dueño) | Nadie desde la app |

Nadie borra, con una excepción a RG1: al autorizar un restablecimiento del segundo factor (C17), el
GC elimina los factores de la persona en `mfa_authenticator` (la política solo lo permite con su propia
autorización de los últimos 5 minutos). La constancia queda en `RestablecimientoFactor`: quién lo pidió
y quién lo autorizó, el método y la fecha de verificación de identidad, y el tipo y las fechas de cada
factor invalidado (nunca sus secretos). La lectura por finca relacionada (y no celda por celda) es a propósito: el PEP y los
servicios leen filas que la pantalla no muestra (técnicos de la finca para excluir verificadores, V5,
C19); el alcance exacto lo aplica el PEP. El comentario de verificación de `db/roles_rls.sql` (solo 11
tablas con RLS) quedó desactualizado: `cuentas/test_rls_django.py::MetadatosTests.test_estado_de_rls_por_tabla`
describe el estado real.

### 5.6 Decisiones de esta parte

| # | Decisión | Por qué |
|---|---|---|
| P3-1 | Corrección 3: GC lee O7d acotado por C17 (solo restablecimientos que puede resolver, solo metadatos). | Sin L, el GC no veía lo que debía autorizar; en O5, O7a y O7b ya lee lo que autoriza. |
| P3-2 | C16: el GC puede cerrar la baja de un técnico con una sincronización final vacía, que revoca sus dispositivos y deja la cuenta INACTIVA. | Sin PWA sin conexión (D3) no hay registros que sincronizar; el GC queda como actor en la bitácora. |
| P3-3 | H1: n(s) incluye la petición; μ(s) usa el historial propio solo con 90 días (si no, la media del rol que concede; sin historial, 0) y no cuenta la última hora; tras resolver una H1, n(s) cuenta desde la resolución. La suspensión es de la persona (también como PF). | Umbral `max(3·μ, 25)` sin que la propia ráfaga lo suba ni lo bloquee de nuevo al resolver. |
| P3-4 | Una lista cuenta como una lectura para H1, y el motor decide la lista con las condiciones del sujeto; las de alcance se aplican como filtro y una prueba compara la lista con la lectura objeto por objeto. | Listas eficientes sin perder la decisión del motor. |
| P3-5 | Las alertas que provoca una negación (H1) se crean al terminar la petición, fuera de la transacción revertida. | Si no, el rollback las borraba. |
| P3-6 | AC·O1b CR no tiene acción propia: el alta y el cambio de datos personales van por `propietario:registrar_datos` (MO, C20). | Así lo implementa el servicio de catálogo; está anotado en la tabla de cobertura. |
| P3-7 | C19 MO (terminar una asignación) no exige que el técnico siga siendo TC. | Para poder terminar la asignación de quien perdió el rol. |
| P3-8 | C13 CI: una versión enmendada DEVUELTA no bloquea el cierre del período. | Igual que `mediciones.services.cerrar_periodo`; la original ya está aprobada. |
| P3-9 | C11: se enmienda la versión aprobada más reciente de la cadena. | Igual que `mediciones.services`. |
| P3-10 | El motivo de devolución (C2) y la justificación (C11) los valida el servicio, no el motor. | Vienen en la petición y no en la base; el motor no recibe textos libres, solo si la justificación está presente. |
| P3-11 | Una sola ventana de auditoría por cuenta AE; al vencer, la tarea diaria deja la cuenta INACTIVA, termina su rol AE y cierra sus sesiones. | Una cuenta AE nominal por auditoría; un AE nunca tiene registros de campo pendientes, así que no pasa por EN_BAJA. |
| P3-12 | RLS: cada `WITH CHECK` repite su condición completa (migración 0019), y las lecturas dentro de funciones `SECURITY DEFINER` usan una fase interna para que una política no se llame a sí misma (0023). | PostgreSQL combina las políticas permisivas de UPDATE (el `USING` de una con el `WITH CHECK` de otra), lo que permitía, por ejemplo, reactivar una cuenta EN_BAJA; y sin la fase interna la recursión dependía del orden de evaluación. |
