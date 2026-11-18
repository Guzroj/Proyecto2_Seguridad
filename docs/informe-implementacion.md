# Informe de implementación — Verdeazul Carbono S.A.

CE-1115 Seguridad de la Información · Proyecto Parte 2 · Grupo 05 · Borrador de trabajo

> **Estado de este borrador.** Las secciones de certificados (O6), bitácora (O8), exportaciones y
> sus pruebas (Persona 4) están completas y respaldadas por código y pruebas del repositorio. Las
> marcadas **PENDIENTE** necesitan evidencia de despliegue o el aporte de otros integrantes y **no
> deben darse por hechas**: lo que aquí se dice de Cloud Run, Secret Manager, Neon y Dependabot sale
> del README del repositorio y de `.github/`, no de una verificación en la consola.

---

## 1. Arquitectura desplegada

Componentes (según `README.md` §1 y `.github/workflows/deploy.yml`; **PENDIENTE**: confirmar con
capturas de la consola de GCP y de Neon):

```
Internet ──HTTPS (TLS de Cloud Run)──▶ verdeazul (Cloud Run, público)
                                        Django 5.2 + gunicorn, imagen "app"
                                        PEP: autorizacion/pep.py, cliente.py, listas.py
                                        Servicios de dominio, pantallas, bitácora, certificados
                                        │                         │
                     token OIDC (audiencia = URL) │               │ TLS (sslmode=require)
                                        ▼                         ▼
                  verdeazul-autorizador (Cloud Run,      Neon PostgreSQL
                  privado, sin allUsers): PDP FastAPI    rol app_runtime (NOBYPASSRLS, no dueño)
                  matriz.py, reglas.py, condiciones.py   RLS con FORCE en todas las tablas del dominio
                  hbac.py — sin acceso a la base         disparadores de invariantes (D22)
                                                          bitácora encadenada, certificados
Secret Manager ──variables de entorno──▶ verdeazul (SECRET_KEY, DATABASE_URL, MFA_CLAVE_CIFRADO,
                                          CERTIFICADO_CLAVE_PRIVADA, ...)
Artifact Registry: imágenes app y autorizador con etiquetas v1.0.0 y sha-<commit>
Cloud Run Jobs: verdeazul-migrate (rol dueño) y verdeazul-tarea-diaria (Cloud Scheduler)
```

La frontera de confianza de la Parte 1 se conserva: el navegador no es confiable; Django es el punto
de aplicación (PEP) y nunca decide solo; el motor (PDP) es un servicio aparte que no ve la base ni datos
personales (recibe ids, estados y conjuntos de ids); la base revalida lo crítico aunque la aplicación
falle (RLS + disparadores).

**Flujo de la acción crítica (aprobar una medición):** `mediciones/services.py:aprobar_medicion`
bloquea la medición (`SELECT … FOR UPDATE`) → el PEP arma los atributos desde la base → el motor aplica
C2, H4 y la edad del segundo factor (RG6) → el servicio revalida estado, verificador asignado e
independencia → `UPDATE` (el disparador de `mediciones/0003` vuelve a revisar H4 y C2) → evento en la
bitácora → `certificados.servicios.emitir` en **la misma transacción** firma y guarda el certificado (o
aplica P2) → `COMMIT`. Si cualquier paso falla, no queda ni la aprobación, ni el certificado, ni el
evento de éxito; la negación sí queda en la bitácora.

## 2. Controles obligatorios implementados

| Control | Dónde está | Cómo se verifica |
|---|---|---|
| Contraseñas con hash lento | `verdeazul/settings.py`: `Argon2PasswordHasher` primero, mínimo 12 caracteres | `cuentas/tests.py`, `cuentas/test_login.py` |
| Control de acceso en el servidor | Motor (`autorizacion/reglas.py`, `condiciones.py`, `hbac.py`) + PEP + servicios + RLS y disparadores (D22) | `autorizacion/test_cobertura.py::CeldaPorCeldaTests` (cada rol × cada acción); pruebas de pantallas que llaman la URL directo (403/404/405) |
| Secretos fuera del código | `.env` ignorado por git; `settings.py` lee todo del entorno; Secret Manager en Cloud Run | `git ls-files` no contiene `.env`; **PENDIENTE** captura de Secret Manager |
| TLS en todo el tráfico | `SECURE_SSL_REDIRECT`, HSTS, cookies `Secure` y `__Host-` sin `DEBUG`; `ssl_require` hacia Neon; TLS de Cloud Run | **PENDIENTE** captura del sitio por HTTPS |
| Validación de entradas | ORM parametrizado; listas blancas en servicios y formularios; geometría validada (D2); filtros de pantallas de lista blanca | pruebas de servicios (`DatoInvalido`), `catalogo/test_traslape.py` |
| Firma digital de certificados | `certificados/formato.py` (Ed25519), `certificados/firma.py` (clave), `certificados/servicios.py` (emisión) | `certificados/test_certificados.py::ManipulacionTests`, `EmisionTests`; `certificados/test_pantallas.py::VerificacionPublicaTests` |

### 2.1 Certificados firmados (O6) — Persona 4

**Emisión.** El gancho `on_medicion_aprobada` (`certificados.servicios.emitir`, conectado en
`settings.VERDEAZUL_GANCHOS`) corre dentro de la transacción de aprobación con la medición ya `APROBADA`
y bloqueada; no la modifica (H4). Toma un candado consultivo por (parcela, período), lee el certificado
vigente con `FOR UPDATE` y:

- medición original: emite; si ya hay un `EMITIDO` para la parcela y el período, niega y todo se revierte (V6);
- versión enmendada (P2): si las toneladas recalculadas por el servidor difieren de las del vigente, lo pasa a
  `REVOCADO` (con la versión que lo causó y el motivo) y emite uno nuevo con `reemplaza_a`; si no difieren, el
  vigente se mantiene y la corrección queda en la bitácora (`certificado:mantener`).

Los datos del titular se obtienen solo con `autorizacion.procesos.datos_propietario_para_certificado`
(el VF no tiene O1b; la función de la base los entrega solo al verificador de esa medición). Sin ellos,
o sin clave de firma, la emisión lanza un rechazo del dominio y la aprobación se revierte (fallo cerrado).

**Documento y firma.** JSON `verdeazul-certificado/1` con `contenido` (certificado, emisor con la huella
de la clave, propietario, finca, parcela con versión y huella de la geometría, período, medición,
toneladas y método) y `firma` (algoritmo, clave pública Ed25519 incrustada, su huella y la firma). Lo
firmado es determinista: prefijo de dominio + JSON canónico (claves ordenadas, sin espacios, decimales
como texto). Se guarda el texto exacto firmado, así el archivo se reconstruye byte a byte.

**Verificación y confianza.** Verificar la firma con la clave incrustada solo prueba integridad: un
falsificador puede firmar con su propia clave e incrustarla. Por eso la verificación devuelve por
separado *íntegro* (la firma corresponde al contenido) y *confiable* (la huella de la clave está entre
las publicadas por Verdeazul en `CERTIFICADO_CLAVES_CONFIABLES`, servidas por HTTPS en
`/certificados/clave-publica/` y en el README), y solo es *válido* si se cumplen ambas. El verificador es
`certificados/formato.py`, sin Django ni base: un comprador lo corre con
`python certificados/formato.py certificado.json --huella <huella>`. La página pública
`/certificados/verificar/` hace lo mismo y, si el documento es válido, consulta su estado (vigente o
revocado) con `certificado_estado_publico`, que solo responde si el id **y** la firma coinciden. Como es
anónima y cada verificación escribe en la bitácora, tiene límite de tasa (10 por minuto y 60 por hora por
IP, 300 por minuto por instancia; 429 con `Retry-After` y un solo aviso en la bitácora por ventana).

**Base de datos.** Índice único parcial `certificado_vigente (parcela, periodo) WHERE estado = 'EMITIDO'`;
disparadores (`certificados/migrations/0002`): solo nace `EMITIDO` de una medición `APROBADA` con la
misma parcela, período, geometría, toneladas y propietario, cuyo documento declara el mismo id y
toneladas, y aprobada por el sujeto de la conexión (C2); la única modificación es `EMITIDO → REVOCADO`
por una versión enmendada aprobada de la misma parcela y período con otras toneladas; nunca se borra.
RLS con FORCE: GC y AE leen todo, el PF los suyos (`EMITIDO` o `REVOCADO`, C4) y el VF solo durante la
emisión de su aprobación (fase `emision`, D28).

### 2.2 Bitácora (O8) — Persona 4

`bitacora.servicios.registrar_evento` reemplaza a la implementación provisional que solo escribía en el
log. Cada evento guarda actor y su nombre, roles vigentes, acción, recurso, resultado (`OK`, `CONCEDIDO`,
`DENEGADO`), antes y después (sin secretos ni datos personales: claves sensibles reemplazadas por
`[omitido]` y textos recortados), detalle, IP de origen, hora del servidor de base de datos y el
encadenamiento.

- **Encadenamiento en la base:** un disparador `BEFORE INSERT` (SECURITY DEFINER, fase interna) toma un
  candado consultivo, lee la cabeza y fija `secuencia`, `momento`, `hash_anterior` y
  `hash = SHA-256(hash_anterior ‖ can(e))`, con `can(e)` el texto de un `jsonb` determinista. La aplicación
  no elige su lugar en la cadena. Solo se escribe en `READ COMMITTED` (con una instantánea de transacción
  la cabeza podría ser vieja). `UNIQUE(hash_anterior)` impide confirmar una bifurcación aunque algo fallara.
- **Solo inserción:** disparador contra UPDATE y DELETE (también para el dueño); `app_runtime` sin UPDATE,
  DELETE ni TRUNCATE (`grant_after_migrate.sql` los vuelve a quitar). RLS con FORCE: leen GC y AE.
- **Transacciones:** los eventos de un cambio se insertan en su transacción (si el cambio se revierte, el
  evento también; si el evento no se puede insertar, el cambio no ocurre — RG4). Las negaciones llegan desde
  `nucleo.transicion` fuera de la transacción revertida; si aun así llegan dentro de una transacción ajena
  durante una petición, `BitacoraPendientesMiddleware` las inserta al terminar la petición.
- **RG4:** el PEP (`autorizacion/cliente.py`) registra toda lectura concedida de O1, O4, O6 y O8; si no
  puede, niega la lectura.
- **Verificación:** `bitacora_verificar()` recalcula cada hash y enlace y reporta huecos, enlaces rotos y
  contenidos alterados; está en `/gestion/bitacora/verificar/` y en `manage.py verificar_bitacora`.
- **Sellos externos (§4.5):** la cadena sola no detecta a quien administre la base y la reescriba entera
  recalculando todos los hashes, o corte el final. Cada hora el Cloud Run Job `verdeazul-sellar-bitacora`
  verifica la cadena y, si está íntegra, firma su cabeza (secuencia y hash) con la clave Ed25519 de
  Verdeazul, que no está en la base. El sello se guarda en `bitacora_sellobitacora` (solo inserción, RLS) y
  se escribe en Cloud Logging. La verificación comprueba la firma de cada sello y que el evento sellado
  exista con el mismo hash: una reescritura o un corte lo delatan (probado en
  `bitacora/test_bitacora.py::SellosTests`). Si la cadena está alterada, el Job no sella y termina con error.

### 2.3 Exportaciones (X, H3) — Persona 4

`exportaciones/servicios.py:exportar` (pantalla `/gestion/exportaciones/`, por POST con CSRF): filtra por
el alcance de los roles del sujeto con X en esa columna, **cuenta N real**, llama a
`autorizar(usuario, "<objeto>:exportar", None, {"registros": N})` (el motor aplica la celda, C5 y H3; sin
`registros` niega), lee exactamente N filas de una lista blanca de campos (nunca hashes, secretos TOTP,
llaves ni el contenido de un factor), escribe un CSV con `cuentas.hbac.marca_de_agua(usuario)` en la
primera línea, en una columna de cada fila y en la última línea, neutraliza la inyección de fórmulas, y
registra la exportación con N y el SHA-256 del archivo. Las negaciones (otro rol, fuera de ventana, H3 sin
segundo factor) quedan en la bitácora.

## 3. Gestión de secretos

| Secreto | Dónde vive | Cómo llega | Quién lo usa |
|---|---|---|---|
| `SECRET_KEY` | Secret Manager `verdeazul-secret-key` | variable de entorno | Django |
| `DATABASE_URL` (rol `app_runtime`) | `verdeazul-db-url` | variable de entorno | Django |
| `DATABASE_URL` (rol dueño) | `verdeazul-migrate-db-url` | variable de entorno | job de migraciones |
| `MFA_CLAVE_CIFRADO` | `verdeazul-mfa-clave-cifrado` | variable de entorno | cifrado de secretos TOTP |
| `CERTIFICADO_CLAVE_PRIVADA` (nuevo) | `verdeazul-certificado-clave` | variable de entorno (`deploy.yml`) | firma de certificados |
| Contraseñas y TOTP de prueba | variables de entorno al correr `crear_usuarios_prueba` | nunca en el código ni en la línea de comandos | — |

La clave de firma se genera con `manage.py generar_clave_certificados`; no se escribe en la base, la
bitácora, los registros ni el repositorio; la huella (pública) se publica. Las pruebas usan una clave
efímera (`verdeazul/pruebas.py`). **PENDIENTE:** crear `verdeazul-certificado-clave`, dar acceso a
`verdeazul-app` y capturar la configuración.

## 4. Seguridad heredada de la plataforma — **PENDIENTE (responsable de despliegue)**

Lo que ya documenta el README y debe confirmarse con capturas: conexión con `sslmode=require`; la
aplicación usa `app_runtime`, creado por SQL con `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`
(los roles creados por la consola de Neon traían `BYPASSRLS` y `neon_superuser`, lo que anulaba RLS);
el rol dueño solo se usa en el job de migraciones; advertencia de no habilitar Neon Auth.

Falta redactar, con evidencia: qué protege Neon por defecto (cifrado en reposo y en tránsito,
aislamiento del proyecto, respaldos y PITR, autenticación de la conexión), restricción de IP de origen
(si se configuró), ramas o entornos separados, y qué queda del lado del equipo. Riesgos que siguen siendo
nuestros aunque la plataforma sea segura: una consulta mal construida, un permiso excesivo (de ahí
`NOBYPASSRLS` y FORCE), una credencial filtrada (la cadena de `app_runtime` puede fijar `app.usuario_id`:
RLS protege contra errores de la aplicación, no contra quien ya ejecuta SQL con ese rol), y el dueño de
la base, que puede saltarse disparadores (la cadena de la bitácora lo delata; ver D23).

## 5. Desviaciones respecto al diseño

La tabla completa (D1–D32) está en `README.md` §4, §5.6 y §6.3. Las de mayor implicación:

**D18 — Hallazgo de RLS en la migración 0019.** Con varias políticas permisivas de UPDATE sobre una
tabla, PostgreSQL exige que la fila vieja cumpla el `USING` de *alguna* y la nueva el `WITH CHECK` de
*alguna*, no necesariamente la misma. Una fila podía entrar por una política estrecha (p. ej. el cierre
de la baja que hace un GC) y salir por el `WITH CHECK` amplio de otra, lo que permitía, por ejemplo,
reactivar una cuenta `EN_BAJA`. La migración `cuentas/0019` lo cierra en dos capas: cada `WITH CHECK`
repite el rol y la condición de su propia política, y disparadores fijan lo que nunca cambia (la máquina
de estados de la cuenta; quién, qué rol y desde cuándo en un rol; una sesión cerrada no se reabre).
Además (`cuentas/0023`), las lecturas dentro de funciones `SECURITY DEFINER` usan una fase interna
(`app_fase_interna()`) para que una política nunca vuelva a llamarse a sí misma: sin ella, la recursión
dependía del orden en que PostgreSQL evaluara las políticas. Lo prueban
`cuentas/test_rls_actualizaciones.py` y `cuentas/test_rls_dominio.py` (funciones con un dueño sin
superusuario, como en Neon). La bitácora y los certificados siguen el mismo patrón (fases internas
`bitacora`, `verificacion` y `emision`, y `WITH CHECK` autosuficientes).

**D19 — Vistas que perdían filas bajo RLS.** Las pantallas de O1–O5 muestran nombres de cuentas y
períodos sin `JOIN`: bajo RLS, un `JOIN` a una fila que el sujeto no puede leer (el PF no tiene O2; el VF
no lee la cuenta del técnico) hacía desaparecer la fila entera de la lista o fallar la ficha. Los nombres
se resuelven aparte con lo que el sujeto sí puede listar y lo demás se muestra como `#id`
(`mediciones/test_pantallas.py::PantallasBajoRLSTests`). Las pantallas de certificados aplican la misma
regla: lo que muestran sale del propio documento firmado, sin `JOIN` a períodos ni cuentas.

**D22 — Defensa en tres capas.** El motor decide (matriz, condiciones, HBAC), el servicio revalida las
invariantes del dominio (estado, H4, separación de funciones) y la base vuelve a revalidar con
restricciones, RLS y disparadores (`catalogo/0003`, `mediciones/0003`, y ahora `certificados/0002` y
`bitacora/0002`), aunque alguien se salte el código. Límite documentado: C2 y C3 se comprueban contra el
sujeto de la conexión, que solo existe en peticiones web. En O6 la base además impide la doble emisión,
cualquier cambio a un certificado salvo su revocación por P2, y el borrado; en O8, cualquier UPDATE o
DELETE. Lo prueban `mediciones/test_disparadores.py`, `certificados/test_certificados.py::DisparadoresTests`
y `bitacora/test_bitacora.py::CadenaTests` atacando con SQL directo como dueño.

**Desviaciones de Persona 4 (D23–D32).** Bitácora encadenada en un disparador de la misma base en vez de
instancia aparte con relé, con sellos horarios firmados que van a Cloud Logging en lugar de a Wazuh y
respaldos fuera del sitio; clave de firma en Secret Manager como
variable de entorno en vez de Transit no exportable; autenticidad por huella publicada; estado `REVOCADO`
de la matriz en lugar del `REEMPLAZADO` de la arquitectura, y el PF ve sus revocados (C4); sin datos O1b
no se certifica; fase RLS `emision`; RG4 registrado en el PEP; verificación pública sin cuenta, con límite de tasa por
instancia; exportación por POST con N comprobado; TRUNCATE cubierto por privilegios.

## 6. Cobertura de la matriz

`docs/cobertura-matriz.md` (generada con `manage.py tabla_cobertura`) tiene una fila por cada una de las
78 celdas: la celda, las acciones que la ejercen, dónde se aplican y qué pruebas lo verifican; una prueba
falla si la tabla no está al día o si cita una función o prueba que no existe. Con Persona 4, O6 y O8 ya
no figuran como "pantalla pendiente": GC·O6 L, PF·O6 L [C4], AE·O6 L/X, GC·O8 L y AE·O8 L/X apuntan a
`certificados/pantallas.py`, `bitacora/pantallas.py` y `exportaciones/`, y la X de las 13 columnas del AE a
`exportaciones/servicios.py:exportar`. Las celdas `—` de O6 y O8 (TC, VF, AC; PF en O8) se niegan por RG1
en el motor y además por RLS.

## 7. Evidencia

Resultados de pruebas reales (Docker Compose local, 17-nov-2026):

| Suite | Antes de Persona 4 | Con Persona 4 |
|---|---|---|
| `manage.py test` (descubrimiento por defecto) | 397 OK | incluida abajo |
| Módulos de `autorizacion/` (no los descubre la corrida por defecto) | 198 OK, 5 saltadas | incluida abajo |
| Todo junto (`manage.py test . autorizacion.test_*`) | 595 | **663 OK, 5 saltadas** |
| Autorizador (`unittest discover -s autorizacion`) | 109 OK, 7 saltadas | 109 OK, 7 saltadas |

Pruebas nuevas (68): emisión firmada; reversión si falla la emisión; doble emisión bloqueada por servicio y
por índice; P2 con y sin cambio de toneladas y en cadena; manipulación del contenido, de la firma y
re-firma con otra clave; JSON con claves duplicadas; verificador independiente; disparadores de O6 con SQL
directo; C4 y V8; RLS de O6 con el rol de la aplicación, incluida la emisión P2; bitácora persistente y
encadenada; manipulación detectada; solo inserción; negaciones que sobreviven al rollback; concurrencia
(4 hilos, 60 eventos, sin huecos ni bifurcaciones); minimización; permisos y RLS de O8; RG4; sellos (reescritura completa y corte del final detectados, sello falsificado, solo inserción, RLS); límite de tasa de la verificación pública; exportaciones
con N real, marca de agua, H3, C5, roles sin X e inyección CSV; pantallas de punta a punta con segundo factor.

Capturas para el informe y la presentación (**PENDIENTE** de tomarlas sobre la URL pública):

1. Sitio por HTTPS (candado y URL `*.run.app`).
2. Secret Manager con los secretos, incluido `verdeazul-certificado-clave` (sin mostrar valores).
3. Dependabot activo y la alerta atendida.
4. Certificado verificado: `/certificados/verificar/` con el archivo descargado → "VÁLIDO" y "EMITIDO".
5. Certificado alterado (cambiar `toneladas_co2` en el JSON) → "NO VÁLIDO … alterado".
6. Intento fallido de un rol: un PF que abre el certificado de otro (404) o un TC que abre `/gestion/bitacora/` (403).
7. Bitácora: aprobación, `certificado:emitir`, la enmienda con `certificado:revocar`, y los `DENEGADO` de los pasos anteriores; y `/gestion/bitacora/verificar/` → "Íntegra".
