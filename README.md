# Verdeazul · Plataforma de certificación de carbono

CE-1115 Seguridad de la Información · Proyecto Parte 2 · Grupo 05

Implementación del diseño de la Parte 1 (matriz de control de acceso, HBAC, separación de
responsabilidades) en Django + PostgreSQL, desplegado en Google Cloud Run con base en Neon.

---

## 1. Cómo correrlo en local

Guía paso a paso (primera vez, cuentas de prueba, recorrido en el navegador, pruebas y cómo
probar certificados y bitácora): [`docs/como-correr.md`](docs/como-correr.md).

Requisitos: **Docker Desktop**. No hace falta instalar Python ni Postgres en la máquina
(en Windows, App Control bloquea la DLL de `psycopg`; dentro de Docker no hay problema).

```bash
# 1. Variables de entorno (una sola vez). .env está en .gitignore: nunca se versiona.
cp .env.example .env
#    Completar SECRET_KEY y POSTGRES_PASSWORD con valores aleatorios, p. ej.:
#    python -c "import secrets; print(secrets.token_urlsafe(50))"

# 2. Levantar Postgres 17, el motor de autorización y Django (python 3.12, usuario no root)
docker compose up -d --build

# 3. Crear las tablas (incluye RLS, disparadores y el catálogo de motivos de devolución)
docker compose exec web python manage.py migrate

# 4. Crear las cuentas de prueba, una por rol (ver 5.1: contraseña y secreto TOTP)

# 5. Abrir http://127.0.0.1:8000
```

Sin el contenedor `autorizador` sano no se concede nada (fallo cerrado, RG5), y sin cuentas de
prueba no hay con quién entrar: no hay registro público.

Comandos útiles:

| Para | Comando |
|---|---|
| Cualquier comando de Django | `docker compose exec web python manage.py <comando>` |
| Consola de Django | `docker compose exec web python manage.py shell` |
| Consola SQL | `docker compose exec db psql -U verdeazul -d verdeazul` |
| Correr pruebas | `docker compose exec web python manage.py test` |
| Pruebas del motor (contenedor aparte) | `docker compose exec autorizador python -m unittest` |
| Ver logs | `docker compose logs -f web` |
| Verificar la cadena de la bitácora | `docker compose exec web python manage.py verificar_bitacora` |
| Sellar la cabeza de la bitácora (en producción, cada hora) | `docker compose exec web python manage.py sellar_bitacora` |
| Generar la clave de firma de certificados | `docker compose exec web python manage.py generar_clave_certificados` |
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
   secreto del rol dueño y, en el mismo contenedor, `python manage.py grant_after_migrate`
   (equivale a `psql -f db/grant_after_migrate.sql`).
5. Crea/actualiza el Cloud Run Job `verdeazul-tarea-diaria` (`tarea_diaria_cuentas`, rol
   runtime, fase `sistema`) y su disparador de Cloud Scheduler (`5 0 * * *`,
   `America/Costa_Rica`).

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
| `verdeazul-mfa-clave-cifrado` | `MFA_CLAVE_CIFRADO`: valor aleatorio largo para cifrar los secretos TOTP | `verdeazul` |
| `verdeazul-certificado-clave` | `CERTIFICADO_CLAVE_PRIVADA`: clave Ed25519 (32 bytes en base64) que firma los certificados. **Debe existir antes de desplegar** (ver §6.2) | `verdeazul` |
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
`FORCE ROW LEVEL SECURITY`** en las tablas del dominio que ya existen; las migraciones de
`cuentas` (`0005`–`0024`) completan el RLS del resto, de modo que las políticas se aplican
incluso al dueño de las tablas y ningún camino del código puede saltarlas.
`db/grant_after_migrate.sql` vuelve a otorgar los privilegios a `app_runtime` después de cada
`migrate`, porque las tablas nuevas no heredan permisos; el pipeline lo ejecuta automáticamente
al final del job de migraciones.

> **Neon Auth:** no habilitarlo si no se usa. Crea los roles `authenticated`, `anonymous` y
> `authenticator`, y les concede DML por defecto sobre `public` (incluidas tablas de Django sin
> RLS, como `django_session`). Si se habilitó por error, revocar esos privilegios y desactivarlo.

#### Service accounts (mínimo privilegio)

| Service account | Usada por | Roles |
|---|---|---|
| `verdeazul-app` | servicio `verdeazul` y Cloud Run Job de la tarea diaria | `secretAccessor` en los 5 secretos de runtime; `run.invoker` sobre el autorizador y sobre el job de la tarea diaria (lo invoca Cloud Scheduler con OIDC). |
| `verdeazul-autorizador` | servicio `verdeazul-autorizador` | Sin roles de proyecto ni acceso a secretos. |
| `verdeazul-migrate` | Cloud Run Job `verdeazul-migrate` | `secretAccessor` solo en `verdeazul-migrate-db-url` y `verdeazul-secret-key`. |
| `verdeazul-deploy` | GitHub Actions (WIF) | `run.admin`, `artifactregistry.writer`, `cloudscheduler.admin`, `browser`, `secretAccessor` en los 6 secretos y `iam.serviceAccountUser` sobre las 3 SA de runtime. |

El autorizador se despliega con `--no-allow-unauthenticated` y **sin IAP**: Cloud Run IAM valida
el token OIDC que `verdeazul` pide al metadata server con `audience = AUTORIZADOR_URL`.

#### Dependabot

`.github/dependabot.yml` mantiene actualizadas las dependencias (pip y Docker) con
PRs automáticos, reduciendo la ventana de exposición a vulnerabilidades conocidas.

- Todos los PRs apuntan a `develop` (`target-branch`), nunca directo a `main`.
- Se ignoran las **actualizaciones mayores**: la arquitectura fija Django 5.2 LTS y
  Python 3.12, así que Django 6.x y Python 3.13+ rompen compatibilidad (por eso los PRs
  de Django 6.1 y Python 3.15 se cierran sin mergear). Las **alertas/PRs de seguridad**
  no se ignoran.
- Las alertas se revisan en *Security → Dependabot alerts* y se atienden actualizando la
  dependencia afectada (o documentando el riesgo aceptado).

#### Despliegue de validación actual

| Recurso | Valor |
|---|---|
| App pública | `https://verdeazul-444812071990.us-east1.run.app` |
| Autorizador privado | `https://verdeazul-autorizador-444812071990.us-east1.run.app` |
| Proyecto / región | `project-4d85c806-d471-4964-b73` / `us-east1` |
| Imagen desplegada | `manual-202611121557` (validación manual) |

El autorizador quedó con `roles/run.invoker` solo para `verdeazul-app`. El path `/healthz`
de FastAPI queda sombreado por el frontend de Cloud Run; para probar el motor usar
`POST /autorizar`, que devuelve la decisión en JSON (`resultado`, `regla`, `motivo`, `rol`,
`roles`, `hbac`, `alertas`, `marca_agua`); una solicitud mal formada da 422. Los usuarios con
un rol los resuelve Django leyendo la base, no el autorizador.

#### Prerrequisitos (setup manual, una sola vez)

1. **Neon**: crear el proyecto (PostgreSQL 18, región `aws-us-east-1`) y la base `neondb`;
   anotar las cadenas **directas** con `sslmode=require` (dueño para migrar, `app_runtime` para runtime).
2. **Artifact Registry**: crear el repo Docker `verdeazul` en `us-east1` (misma región que Cloud Run).
3. **Cloud Run**: crear los dos servicios con las SA dedicadas (`verdeazul-app` y
   `verdeazul-autorizador`); el job de migraciones usa `verdeazul-migrate`. Dar al autorizador
   solo el permiso de invocación a `verdeazul-app`.
4. **Secret Manager**: cargar los seis secretos de la tabla anterior (incluido
   `verdeazul-mfa-clave-cifrado`, un valor aleatorio largo; no derivarlo de `SECRET_KEY`
   para que rotar la clave de Django no invalide el segundo factor).
5. **Base de datos**: crear `app_runtime` por SQL (ver *Dos roles*), ejecutar `db/roles_rls.sql`
   con el rol dueño y `db/grant_after_migrate.sql` después de `migrate`.
6. **Tarea diaria**: habilitar `cloudscheduler.googleapis.com`, dar `roles/cloudscheduler.admin`
   a `verdeazul-deploy` y `roles/iam.serviceAccountUser` sobre `verdeazul-app` al agente
   `service-<número-de-proyecto>@gcp-sa-cloudscheduler.iam.gserviceaccount.com` (es quien emite
   el token OIDC del disparador).
7. **GitHub**: registrar el pool/proveedor de WIF (`github` / `github-oidc`, condición
   `assertion.repository == 'Guzroj/Proyecto2_Seguridad'`). Las variables del workflow
   (proyecto, región, provider y service account) son opcionales: trae fallback para este
   proyecto. Sin claves JSON de larga vida.

---

## 2. Estructura

```
verdeazul/        configuración (settings lee TODO de variables de entorno) y rutas
nucleo/           piezas compartidas: errores, vocabulario de acciones, ganchos, decorador transicion
cuentas/          O7–O9: cuentas y estados, roles con vigencia, sesiones, segundo factor, ventanas de
                  auditoría, dispositivos de campo, restablecimientos, alertas HBAC, RLS (migraciones)
                  y pantallas de gestión (/gestion/cuentas, roles, ventanas, ...)
bitacora/         O8: bitácora encadenada (SHA-256) de solo inserción; pantallas de solo lectura
certificados/     O6: emisión firmada con Ed25519 (gancho on_medicion_aprobada), P2, verificación
  formato.py      firma y verificación sin Django: también es el verificador independiente
exportaciones/    X: exportaciones del auditor con H3, N real y marca de agua (CSV)
autorizacion/     motor de autorización
  pep.py, cliente.py, listas.py, procesos.py   PEP (Django): atributos leídos de la base, listas filtradas
  app.py, matriz.py, reglas.py, condiciones.py, hbac.py   PDP (FastAPI, contenedor aparte, sin base)
catalogo/         O1: Finca, Parcela, ParcelaVersion (O1a), DatosPropietario (O1b)
  services.py     única vía para cambiar el catálogo (incluye el chequeo de traslape de C20 con shapely)
  pantallas.py    /gestion/fincas, /gestion/parcelas
mediciones/       O2–O5: Periodo, Asignacion, Medicion + MedicionActor, Enmienda, MotivoDevolucion
  services.py     única vía para cambiar estados (ninguna vista hace .save() directo)
  selectores.py   funciones de atributos de la matriz: actores(m), tecnicos(f,p), ...
  carbono.py      cálculo de toneladas de CO2
  pantallas.py    /gestion/periodos, asignaciones, mediciones, enmiendas
templates/        plantilla base y menú según los roles vigentes
db/               SQL de roles y privilegios para Neon (app_runtime, RLS)
docs/             tabla de cobertura de la matriz (generada con manage.py tabla_cobertura)
```

Migraciones con SQL propio: `cuentas` (RLS de todas las tablas), `catalogo/0003` y
`mediciones/0003` (disparadores), `mediciones/0002` (catálogo de motivos de devolución).

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

Configuración actual: `autorizar` → `autorizacion.cliente.autorizar` (motor real, también en local;
solo se concede lo que la matriz permite), `usuarios_con_rol` → `cuentas.roles.usuarios_con_rol`
(cuentas ACTIVAS con el rol vigente), `registrar_evento` → `bitacora.servicios.registrar_evento`
(bitácora persistente y encadenada) y `on_medicion_aprobada` → `certificados.servicios.emitir`
(certificado firmado; ver §6).
Sin un gancho `autorizar` configurado, la implementación provisional **niega todo** (fallo cerrado).

### 3.3 Vocabulario de acciones (`nucleo/acciones.py`)

`finca:crear|modificar|baja` · `parcela:crear|modificar_geometria|baja` · `propietario:crear_datos|registrar_datos` ·
`periodo:abrir|pasar_verificacion|cerrar` · `asignacion:crear|terminar` ·
`medicion:crear|modificar|enviar|aprobar|devolver|sincronizar` · `enmienda:solicitar|autorizar|rechazar` ·
`cuenta:crear|modificar|baja|activar` · `ventana:autorizar` · `rol:asignar|retirar|autorizar|rechazar` ·
`dispositivo:registrar|revocar` · `factor:registrar|solicitar_restablecimiento|autorizar_restablecimiento|rechazar_restablecimiento` ·
`alerta:crear|resolver`

Además, para cada una de las 13 columnas: `<objeto>:leer` (L de un objeto), `<objeto>:listar` (L de
una lista) y `<objeto>:exportar` (X), con `<objeto>` en `finca`, `parcela`, `propietario`, `periodo`,
`asignacion`, `medicion`, `enmienda`, `certificado`, `cuenta`, `rol`, `dispositivo`, `factor`,
`bitacora` y `alerta`.

Cada constante indica en un comentario su celda de la matriz (p. ej. `MEDICION_APROBAR` → VF·O4 AP, C2);
`autorizacion/matriz.py` traduce cada acción a su columna y operación.

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

Además, **disparadores** (`catalogo/0003`, `mediciones/0003`) que aplican a cualquiera que escriba,
incluido el dueño de la base:

| Tabla | Qué rechaza |
|---|---|
| Medición | Cualquier cambio a una APROBADA o de un período CERRADO (H4); transiciones fuera de BORRADOR → ENVIADA → APROBADA \| DEVUELTA → ENVIADA; cambiar parcela, geometría, período, autor u origen; editar datos de campo fuera de BORRADOR/DEVUELTA o en una versión enmendada; asignar verificador fuera del envío. Al aprobar o devolver, si la conexión tiene sujeto: que no sea el verificador asignado, que sea actor de la cadena, que haya sido técnico de la finca y período o que sea su propietario (C2). Al crear: original fuera de BORRADOR o de un período ABIERTO; versión de geometría de otra parcela. |
| Historial de actores | Toda modificación o borrado (solo inserción). |
| Período | Cambios sobre uno CERRADO, reabrirlo o saltarse EN_VERIFICACION; cerrarlo con mediciones pendientes (C13). |
| Enmienda | Cambiar lo solicitado; transiciones fuera de su ciclo; cambiar quién la resolvió; que la resuelva otra cuenta que la conectada (C3). |
| Asignación | Asignar a un propietario su propia finca o en un período CERRADO; cambiarla salvo terminarla, una vez (C19). |
| Versión de geometría | Todo cambio salvo cerrar su vigencia, una vez (C20). |
| Finca, parcela, datos del propietario | Cambiar propietario, finca o código; reactivar algo dado de baja. |
| Todas las del dominio | Borrar filas (RG1). `TRUNCATE` lo impiden los privilegios de `app_runtime`. |

Las pruebas de `mediciones/test_disparadores.py` los atacan con SQL directo como dueño de la base.

---

## 4. Decisiones de implementación y desviaciones respecto a la Parte 1

| # | Decisión | Por qué | Implicación de seguridad |
|---|---|---|---|
| D1 | **Cálculo de CO2**: ecuación pantropical de Chave et al. (2014), `AGB = 0.0673·(ρ·D²·H)^0.976` kg por árbol, ρ = 0.6 g/cm³; carbono = 47 % (IPCC 2006); CO2 = C·44/12. Árbol promedio × número de árboles. | La Parte 1 solo dice "el servidor recalcula el carbono con los datos crudos", sin fórmula. | El cliente nunca envía toneladas; el servidor las recalcula al crear, modificar y **otra vez al aprobar**. Simplificación: árbol promedio y stock en pie, no incremento. |
| D2 | Geometría como **GeoJSON validado** (lista blanca) en vez de **PostGIS**; la validez del polígono y el **traslape** (C20) los comprueba el servicio de catálogo con la librería `shapely`. | PostGIS exige GDAL en la imagen y activar la extensión en Neon; `shapely` cierra C20 sin cambiar la base ni la infraestructura. | Se rechaza un polígono que se cruza consigo mismo o sin área, y uno cuyo interior se cruce con la versión vigente de otra parcela activa (compartir un borde está permitido). Un bloqueo de transacción (`pg_advisory_xact_lock`) serializa los cambios de geometría, así dos altas simultáneas no pasan ambas. Riesgo residual: el traslape no lo revalida la base (las versiones sí son inmutables por disparador); quien escriba SQL directo con el rol de la aplicación podría insertar una geometría traslapada. |
| D3 | Sin PWA sin conexión ni paquetes de trabajo firmados. | La captura offline y los paquetes firmados no forman parte de la implementación. | C13 `TV` no verifica paquetes vigentes. C16 se ejerce con la sincronización final vacía que cierra la baja de un técnico (D8). Riesgo R6 no aplica al no haber captura offline. |
| D4 | Verificador **nuevo** en cada envío. | Consecuencia de C2: quien devolvió queda en actores(m) y no puede aprobar. | Ojos frescos tras cada devolución; exige al menos 3 VF activos para un ciclo enviar→devolver→reenviar→enmendar. |
| D5 | On-premise → Cloud Run + Neon; OpenBao → Secret Manager. | Migración a servicios gestionados. | Secretos fuera de la imagen y del repo; TLS terminado por Cloud Run; identidad de servicio vía WIF. |
| D6 | Django corre dentro de Docker también en desarrollo; Python 3.12. | App Control de Windows bloquea `psycopg`; misma versión que producción. | Paridad desarrollo–producción. |
| D19 | Las pantallas de O1–O5 muestran nombres de cuentas y períodos sin `JOIN`: los resuelven aparte con lo que el sujeto puede listar, y lo demás aparece como `#id`. | Bajo RLS, un `JOIN` a una fila que el sujeto no puede leer (PF no tiene O2; VF no lee la cuenta del técnico) haría desaparecer la fila entera de la lista o fallar la ficha. | Las listas coinciden con lo que el motor concede; ningún nombre de una cuenta ajena se filtra por la pantalla. Lo verifican pruebas con el rol real de la aplicación (`mediciones/test_pantallas.py::PantallasBajoRLSTests`). |
| D20 | Las acciones que cambian datos se confirman en una página (GET) y se ejecutan con POST. | Aprobar, devolver y autorizar una enmienda exigen segundo factor de ≤ 300 s; tras el reto (RG6) el sistema vuelve a la página pedida con GET. | Nunca se ejecuta un cambio con GET (CSRF); el usuario confirma de nuevo tras reautenticarse. |
| D21 | Catálogo de motivos de devolución cargado por migración (`mediciones/0002`) en la fase `sistema`. | C2 exige devolver con un motivo de un catálogo cerrado; la tabla tiene RLS con FORCE y solo admite escrituras de procesos del sistema. | Ninguna persona agrega motivos; al revertir, se desactivan en vez de borrarse (RG1). |
| D22 | Disparadores de dominio (`catalogo/0003`, `mediciones/0003`) que revalidan H4, C2, C3, C13, C19, C20 y RG1. | Defensa en profundidad prometida por la arquitectura: la base revalida lo crítico aunque falle la aplicación. | Tres capas: el motor decide, el servicio revalida y la base vuelve a revalidar. Límite: C2 y C3 se comprueban contra el sujeto de la conexión, que solo existe en las peticiones web; en consola o tareas esas dos no aplican (las demás sí). |

## 5. Cuentas, autenticación y motor de autorización

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

### 5.3 Uso desde otros módulos

Esto actualiza lo que dice 3.2 sobre el comportamiento actual de los ganchos:

| Pieza | Uso |
|---|---|
| `autorizar` | Conectado al motor real (`autorizacion.cliente.autorizar`) también en local. Solo se concede lo que la matriz permite; aprobar, devolver y autorizar enmiendas piden segundo factor reciente, así que desde la consola responden `ReautenticacionRequerida`. |
| `usuarios_con_rol` | `cuentas.roles.usuarios_con_rol`: cuentas ACTIVAS con el rol vigente. |
| Listas | `autorizacion.listas.listar(usuario, "fincas")` devuelve un QuerySet ya filtrado al alcance del sujeto (fincas, parcelas, propietarios, periodos, asignaciones, mediciones, enmiendas, cuentas, sesiones, ventanas, roles, solicitudes_rol, dispositivos, restablecimientos, alertas). |
| Lectura de un objeto | `ganchos.autorizar(usuario, "finca:leer", finca)`; fuera de alcance da `FueraDeAlcance` (404). |
| Exportaciones | Pasar `{"registros": N}` en el contexto de `autorizar(..., "<objeto>:exportar", ...)` (si falta, se niega) y poner en el archivo `cuentas.hbac.marca_de_agua(usuario)`. |
| Certificado | `autorizacion.procesos.datos_propietario_para_certificado(medicion)` devuelve nombre e identificación del propietario solo al VF que aprueba esa medición; el VF no tiene acceso directo a O1b. |
| Errores en pantallas | `cuentas.errores.ErroresMiddleware` traduce las excepciones a 403 / 404 / 400 / 409. |
| Pruebas de otros módulos | Para usar el motor real sin levantarlo: `autorizacion.test_cliente.MotorFalso`. Para aislar un servicio: quitar el gancho `autorizar` y activar `AUTORIZACION_PROVISIONAL_PERMISIVA`. |

### 5.4 Producción

| Variable | Valor |
|---|---|
| `AUTORIZADOR_URL` | URL del servicio `verdeazul-autorizador` (ya la resuelve `deploy.yml`). |
| `AUTORIZADOR_AUTENTICACION` | `oidc` (por defecto). `ninguna` solo se acepta con `DEBUG`. |
| `MFA_CLAVE_CIFRADO` | Secreto en Secret Manager para cifrar los secretos TOTP; si falta, se deriva de `SECRET_KEY`. |

- La tarea diaria `tarea_diaria_cuentas` corre como Cloud Run Job (`verdeazul-tarea-diaria`,
  rol runtime, fase `sistema`) disparado por Cloud Scheduler (`5 0 * * *`, zona
  `America/Costa_Rica`). `deploy.yml` crea/actualiza ambos.
- Las migraciones dan `GRANT EXECUTE` a `app_runtime` sobre `cuentas_id_para_login`,
  `cuentas_media_lecturas_rol` y `certificado_datos_propietario` si el rol ya existe. Si el rol
  se creó después de migrar, `db/grant_after_migrate.sql` (que ya corre el pipeline) los otorga
  de forma idempotente como dueño.

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
C19); el alcance exacto lo aplica el PEP. El comentario de verificación de `db/roles_rls.sql`
describe el estado final: **25 tablas con RLS y FORCE**, **5 metadatos de Django con RLS sin FORCE**
(`auth_group`, `auth_group_permissions`, `auth_permission`, `django_content_type`,
`django_migrations`) y **`django_session` sin RLS**. La prueba que lo fija es
`cuentas/test_rls_django.py::MetadatosTests.test_estado_de_rls_por_tabla`.

### 5.6 Decisiones de implementación (cuentas y autorización)

| # | Decisión | Por qué |
|---|---|---|
| D7 | Corrección 3: GC lee O7d acotado por C17 (solo restablecimientos que puede resolver, solo metadatos). | Sin L, el GC no veía lo que debía autorizar; en O5, O7a y O7b ya lee lo que autoriza. |
| D8 | C16: el GC puede cerrar la baja de un técnico con una sincronización final vacía, que revoca sus dispositivos y deja la cuenta INACTIVA. | Sin PWA sin conexión (D3) no hay registros que sincronizar; el GC queda como actor en la bitácora. |
| D9 | H1: n(s) incluye la petición; μ(s) usa el historial propio solo con 90 días (si no, la media del rol que concede; sin historial, 0) y no cuenta la última hora; tras resolver una H1, n(s) cuenta desde la resolución. La suspensión es de la persona (también como PF). | Umbral `max(3·μ, 25)` sin que la propia ráfaga lo suba ni lo bloquee de nuevo al resolver. |
| D10 | Una lista cuenta como una lectura para H1, y el motor decide la lista con las condiciones del sujeto; las de alcance se aplican como filtro y una prueba compara la lista con la lectura objeto por objeto. | Listas eficientes sin perder la decisión del motor. |
| D11 | Las alertas que provoca una negación (H1) se crean al terminar la petición, fuera de la transacción revertida. | Si no, el rollback las borraba. |
| D12 | AC·O1b: el alta de los datos personales del propietario se autoriza como `propietario:crear_datos` (CR, C20) y el cambio como `propietario:registrar_datos` (MO, C20). | Cada operación de la celda se ejerce con su propia acción, así la tabla de cobertura no tiene excepciones. |
| D13 | C19 MO (terminar una asignación) no exige que el técnico siga siendo TC. | Para poder terminar la asignación de quien perdió el rol. |
| D14 | C13 CI: una versión enmendada DEVUELTA no bloquea el cierre del período. | Igual que `mediciones.services.cerrar_periodo`; la original ya está aprobada. |
| D15 | C11: se enmienda la versión aprobada más reciente de la cadena. | Igual que `mediciones.services`. |
| D16 | El motivo de devolución (C2) y la justificación (C11) los valida el servicio, no el motor. | Vienen en la petición y no en la base; el motor no recibe textos libres, solo si la justificación está presente. |
| D17 | Una sola ventana de auditoría por cuenta AE; al vencer, la tarea diaria deja la cuenta INACTIVA, termina su rol AE y cierra sus sesiones. | Una cuenta AE nominal por auditoría; un AE nunca tiene registros de campo pendientes, así que no pasa por EN_BAJA. |
| D18 | RLS: cada `WITH CHECK` repite su condición completa (migración 0019), y las lecturas dentro de funciones `SECURITY DEFINER` usan una fase interna para que una política no se llame a sí misma (0023). | PostgreSQL combina las políticas permisivas de UPDATE (el `USING` de una con el `WITH CHECK` de otra), lo que permitía, por ejemplo, reactivar una cuenta EN_BAJA; y sin la fase interna la recursión dependía del orden de evaluación. |

## 6. Certificados, bitácora y exportaciones (Persona 4)

### 6.1 Qué hay

| Pieza | Dónde | Qué hace |
|---|---|---|
| Bitácora (O8) | `bitacora/` | `registrar_evento` inserta cada evento en `bitacora_evento`. La **base** fija secuencia, hora y `hash = SHA256(hash_anterior ‖ can(e))` en un disparador con candado consultivo (sin bifurcaciones ni huecos; `UNIQUE(hash_anterior)` como respaldo). Solo inserción: un disparador rechaza UPDATE y DELETE aun al dueño; `app_runtime` no tiene UPDATE, DELETE ni TRUNCATE. RLS con FORCE: leen GC y AE. `bitacora_verificar()` y `manage.py verificar_bitacora` recalculan la cadena. **Sellos externos:** cada hora el Job `verdeazul-sellar-bitacora` verifica la cadena y, si está íntegra, firma su cabeza (secuencia y hash) con la clave Ed25519 de Verdeazul; el sello queda en `bitacora_sellobitacora` (solo inserción) y en Cloud Logging. |
| Certificados (O6) | `certificados/` | Al aprobar (`on_medicion_aprobada`, en la misma transacción) se emite un certificado firmado con Ed25519; P2 revoca y reemite si una enmienda cambia las toneladas, o mantiene el vigente si no. A lo sumo un `EMITIDO` por parcela y período (índice único parcial). Disparadores: solo nace `EMITIDO` de una medición `APROBADA` coherente y por su verificador; solo pasa a `REVOCADO` por una versión enmendada aprobada con otras toneladas; nunca se borra. |
| Verificación | `certificados/formato.py` | Sin Django ni base: `python certificados/formato.py certificado.json --huella <huella>`. En el sitio: `/certificados/verificar/` (pública, con límite de tasa: 10 por minuto y 60 por hora por IP, 300 por minuto por instancia; responde 429) y `/certificados/clave-publica/`. |
| Exportaciones (X) | `exportaciones/` | `/gestion/exportaciones/`: el AE exporta cualquiera de las 13 columnas en CSV. Se cuenta N real, el motor decide con `{"registros": N}` (C5, H3), el archivo lleva la marca de agua en la primera línea, en cada fila y al final, y la exportación (o su negación) queda en la bitácora. |
| Pantallas | `/gestion/certificados/`, `/gestion/bitacora/` | Lista, ficha y descarga del documento firmado; lista, detalle y verificación de la bitácora. Ninguna escribe. |

### 6.2 Clave de firma

1. Generar: `docker compose exec web python manage.py generar_clave_certificados`. Imprime la **huella** (pública) y
   la **clave privada** (secreta).
2. Local: poner la privada en `.env` como `CERTIFICADO_CLAVE_PRIVADA=` (`.env` está en `.gitignore`).
3. Producción: crear el secreto `verdeazul-certificado-clave` en Secret Manager con la privada y dar
   `roles/secretmanager.secretAccessor` sobre él a `verdeazul-app`. `deploy.yml` lo monta como variable
   de entorno. **Sin ese secreto el despliegue falla**, y sin clave la aprobación se revierte (fallo cerrado).
4. Publicar la huella (aquí y en `/certificados/clave-publica/`). Al rotar, poner las huellas viejas y la
   nueva en `CERTIFICADO_CLAVES_CONFIABLES` (separadas por coma) para que los certificados viejos sigan verificando.

**Huella publicada de la clave de producción** (SHA-256 de la clave pública Ed25519, fijada en
`CERTIFICADO_CLAVES_CONFIABLES` en `deploy.yml`):

```
14428e8d992fa57dfb65003add2e7332a1c2372b5bd584b7b942fec7dba907c2
```

Verificar un certificado de producción sin el sitio:
`python certificados/formato.py certificado.json --huella 14428e8d992fa57dfb65003add2e7332a1c2372b5bd584b7b942fec7dba907c2`.

Las pruebas no necesitan la clave: `verdeazul/pruebas.py` usa una efímera si no hay una configurada.

### 6.3 Decisiones y desviaciones

| # | Decisión | Por qué | Implicación de seguridad |
|---|---|---|---|
| D23 | La bitácora vive en la misma base (Neon) y se encadena **síncronamente** en un disparador, en vez de una instancia aparte con relé y *outbox* asíncrono (Arquitectura §4.5). | Una sola base gestionada; sin proceso de relé que operar. | Los eventos de un cambio se confirman con el cambio (o ninguno); las negaciones se escriben fuera de la transacción revertida (y, si llegan dentro de una ajena durante una petición, al terminarla). El candado serializa las escrituras de la bitácora (costo aceptado a esta escala). Quien administre la base puede saltarse los disparadores, pero la cadena lo delata; si reescribe la cadena ENTERA recalculando los hashes, o corta el final, lo delatan los **sellos** horarios: no puede firmar uno nuevo sin la clave, que no está en la base. Los sellos van a Cloud Logging (el "Wazuh y respaldos fuera del sitio" de §4.5) en lugar de a un SIEM; un sello solo cubre hasta su hora, así que lo de la última hora queda protegido solo por la cadena. El mismo Job hace la verificación periódica y no sella una cadena alterada. |
| D24 | La clave de firma vive en Secret Manager y llega como variable de entorno, no en Transit de OpenBao (no exportable). | D5: OpenBao → Secret Manager. | La clave está en la memoria del contenedor: un compromiso del servicio la expone. No se registra ni se guarda en la base, la bitácora ni los archivos. |
| D25 | Autenticidad por huella publicada: la verificación separa "firma íntegra" (con la clave incrustada) de "clave de Verdeazul" (huella en `CERTIFICADO_CLAVES_CONFIABLES`, publicada por HTTPS). | Una clave incrustada sola no prueba quién firmó: cualquiera firma un documento falso con su propia clave. | Un documento re-firmado con otra clave sale "íntegro pero no de Verdeazul" y se rechaza. |
| D26 | Estado `REVOCADO` (matriz y P2), no `REEMPLAZADO` (Arquitectura §2.3); el PF ve también sus revocados (C4 de la matriz). | La matriz es el documento verificable celda por celda. | El histórico se conserva y el reemplazo se enlaza con `reemplaza_a`. |
| D27 | Sin datos del propietario (O1b) no se emite y la aprobación se revierte (409). | El certificado identifica al titular; los datos se leen solo por `datos_propietario_para_certificado`. | El VF nunca lee O1b directamente. |
| D28 | Fase RLS `emision`: el VF lee, inserta y revoca certificados solo mientras dura la emisión de su aprobación. | La matriz no le da O6 al VF; la emisión corre con su conexión. | Fuera de la emisión el VF ve cero certificados (probado con el rol de la aplicación). Como las demás fases, la fija la aplicación (límite de `cuentas/rls.py`). |
| D29 | RG4: toda lectura concedida de O1, O4, O6 y O8 la registra el PEP (`autorizacion/cliente.py`) con resultado `CONCEDIDO`; si no se puede registrar, se niega. | RG4 de la matriz. | Más filas en la bitácora (retención: Arquitectura §4.5). |
| D30 | Verificación pública sin cuenta: no lee O6, solo el estado del certificado cuyo id **y** firma coinciden (`certificado_estado_publico`). | Un comprador debe poder verificar sin acceso a Verdeazul (enunciado §2.2) y saber si fue revocado. | No revela nada que no esté en el archivo. Límite de tasa por IP (la última de `X-Forwarded-For`, la que agrega Cloud Run) y por instancia, con 429 y un solo aviso en la bitácora por ventana. Riesgo residual: el contador vive en la caché de cada instancia (con `--max-instances=5`, hasta 5 veces el límite) y detrás de un balanceador que agregue su IP el límite por IP se vuelve global. |
| D31 | Exportación por POST con CSRF; N contado antes de autorizar y comprobado al leer; tope de 50 000 filas; prefijo `'` contra inyección de fórmulas CSV. | Un GET ajeno no debe gastar la cuota H3 del auditor; N debe ser exactamente lo exportado. | Campos de lista blanca: nunca hashes, secretos TOTP, llaves ni el contenido de un factor. |
| D32 | TRUNCATE de bitácora y certificados lo impiden los privilegios de `app_runtime` (y `grant_after_migrate.sql` los vuelve a quitar), no un disparador. | Igual que el resto del dominio; `migrate` y las pruebas vacían tablas como dueño. | Solo el dueño (migraciones) podría truncar. |

### 6.4 Pruebas

`certificados/test_certificados.py`, `certificados/test_pantallas.py`, `bitacora/test_bitacora.py` y
`exportaciones/test_exportaciones.py`. Las de `autorizacion/` no las descubre `manage.py test` sin etiquetas
(la carpeta no tiene `__init__.py`); para correr todo:

```bash
docker compose exec web sh -c 'python manage.py test . $(ls autorizacion/test_*.py autorizacion/tests.py | sed "s#/#.#; s#[.]py##")'
docker compose exec autorizador python -m unittest discover -s autorizacion -p "test_*.py"
```

