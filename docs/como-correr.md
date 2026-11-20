# Cómo correr Verdeazul en local

Guía práctica para levantar la plataforma, entrar con las cuentas de prueba, recorrer el flujo
que emite un certificado y verificar la bitácora. El diseño y las decisiones están en el
[README](../README.md); aquí solo van los pasos.

## 1. Requisitos

- **Docker Desktop** (Docker Compose v2). No hace falta Python ni PostgreSQL en la máquina:
  todo corre en contenedores.
- Git, para clonar el repositorio.

En Windows, App Control puede bloquear la DLL de `psycopg` fuera de Docker; por eso los comandos
de Django se ejecutan dentro del contenedor `web`.

## 2. Primera vez

Desde la raíz del repositorio:

```bash
# 1) Variables de entorno. .env está en .gitignore: nunca se versiona.
cp .env.example .env

# 2) Generar SECRET_KEY y POSTGRES_PASSWORD (una vez) y pegarlos en .env
python -c "import secrets; print(secrets.token_urlsafe(50))"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`.env.example` explica cada variable. Para desarrollo local alcanza con `SECRET_KEY`,
`POSTGRES_PASSWORD` y `DEBUG=true`; `docker-compose.yml` arma `DATABASE_URL` contra el servicio
`db`. La clave de firma de certificados es opcional en local (ver §6).

```bash
# 3) Levantar PostgreSQL 17, el motor de autorización y Django
docker compose up -d --build

# 4) Crear tablas, RLS, disparadores y catálogos
docker compose exec web python manage.py migrate

# 5) Crear las cuentas de prueba (ver §3)
# 6) Abrir http://127.0.0.1:8000
```

Sin el contenedor `autorizador` sano no se concede nada (fallo cerrado). Sin cuentas de prueba no
hay con quién entrar: no existe registro público.

Comandos del día a día:

```bash
docker compose ps
docker compose logs -f web
docker compose exec web python manage.py <comando>
docker compose exec db psql -U verdeazul -d verdeazul
docker compose down          # apagar
docker compose down -v       # borrar también la base local y empezar de cero
```

## 3. Cuentas de prueba

El comando crea una cuenta por rol, todas activas y con la misma contraseña:

| Usuario | Rol |
|---|---|
| `prueba_tc`, `prueba_tc2` | Técnico de campo (TC) |
| `prueba_vf`, `prueba_vf2`, `prueba_vf3` | Verificador (VF) |
| `prueba_gc`, `prueba_gc2` | Gestor de catálogo (GC) |
| `prueba_ac` | Administrador de cuentas (AC) |
| `prueba_ae` | Auditor externo (AE), con ventana de 30 días |
| `prueba_pf` | Propietario de finca (PF), **sin segundo factor** |

La contraseña y el secreto TOTP se leen del entorno, nunca del código ni de la línea de comandos.
Todas las cuentas con segundo factor comparten el mismo secreto: se agrega **una sola vez** en la
app de autenticación con «Ingresar clave de configuración» (TOTP basado en tiempo).

Bash (Linux, macOS, WSL):

```bash
read -rs VZ_PRUEBA_CLAVE && export VZ_PRUEBA_CLAVE
export VZ_PRUEBA_TOTP=$(python3 -c "import base64,secrets;print(base64.b32encode(secrets.token_bytes(20)).decode())")
docker compose exec -e VZ_PRUEBA_CLAVE -e VZ_PRUEBA_TOTP web python manage.py crear_usuarios_prueba
unset VZ_PRUEBA_CLAVE VZ_PRUEBA_TOTP
```

PowerShell (Docker Desktop en Windows):

```powershell
$clave = Read-Host "Contraseña de las cuentas de prueba (mínimo 12 caracteres)" -AsSecureString
$env:VZ_PRUEBA_CLAVE = [System.Net.NetworkCredential]::new("", $clave).Password
$env:VZ_PRUEBA_TOTP = (docker compose exec -T web python -c "import base64,secrets;print(base64.b32encode(secrets.token_bytes(20)).decode())").Trim()
docker compose exec -e VZ_PRUEBA_CLAVE -e VZ_PRUEBA_TOTP web python manage.py crear_usuarios_prueba
Remove-Item Env:VZ_PRUEBA_CLAVE, Env:VZ_PRUEBA_TOTP
```

El comando imprime la URI del TOTP y los códigos de recuperación **una sola vez**. Las cuentas que
ya existen no se tocan. Guarde esos valores fuera del repositorio.

## 4. Recorrido en el navegador: emitir un certificado

1. **`prueba_ac`**: crear una finca con `prueba_pf` como propietario.
2. **`prueba_ac`**: registrar los datos personales del propietario en la ficha de la finca
   (sin ellos la aprobación se revierte con 409).
3. **`prueba_ac`**: crear una parcela con código, área y geometría GeoJSON.
4. **`prueba_gc`**: abrir un período.
5. **`prueba_ac`**: asignar la finca a `prueba_tc` en ese período.
6. **`prueba_tc`**: crear la medición (árboles, DAP y altura) y **enviarla**. El sistema elige un
   VF al azar entre los elegibles.
7. **El VF asignado** (la ficha de la medición lo muestra): **aprobar**. Pide un segundo factor de
   hace 5 minutos o menos. La aprobación emite el certificado firmado en estado `EMITIDO`.
8. **`prueba_gc`** o **`prueba_pf`**: abrir `/gestion/certificados/`, entrar al certificado y
   descargarlo.

No se puede aprobar una medición propia, ni la de una finca de la que se es propietario, ni en la
que se haya participado antes: es la separación de responsabilidades (C2/C3).

## 5. Verificar un certificado

Sin sesión, en `/certificados/verificar/`, suba el archivo descargado. Debe mostrar:

- **VÁLIDO** y **Estado en el registro de Verdeazul: EMITIDO**.
- Si cambia `toneladas_co2` en una copia y la vuelve a subir: **NO VÁLIDO — la firma NO corresponde
  al contenido**.

Sin la web:

```bash
docker compose exec web python manage.py verificar_certificado certificado.json
# o, sin Django ni base:
python certificados/formato.py certificado.json --huella <huella publicada>
```

La huella de la clave de producción está en el README §6.2 y en la página
`/certificados/clave-publica/`. Es pública; la clave privada nunca se publica.

## 6. Certificados y bitácora

Para emitir certificados en local hace falta una clave de firma:

```bash
docker compose exec web python manage.py generar_clave_certificados
```

Copie la clave privada en `.env` como `CERTIFICADO_CLAVE_PRIVADA=...` y reinicie `web`:

```bash
docker compose restart web
```

La huella pública se publica en `/certificados/clave-publica/`. En producción la clave vive en
Secret Manager (`verdeazul-certificado-clave`) y se monta como variable de entorno; el README §6.2
explica la rotación.

Bitácora:

```bash
# Recalcular la cadena completa
docker compose exec web python manage.py verificar_bitacora

# Firmar la cabeza de la cadena (en producción corre cada hora como Cloud Run Job)
docker compose exec web python manage.py sellar_bitacora
```

En la web, `prueba_gc` y `prueba_ae` pueden abrir `/gestion/bitacora/` y
`/gestion/bitacora/verificar/`; debe decir **Íntegra**.

## 7. Pruebas

```bash
# Django (incluye catálogo, mediciones, cuentas, certificados, bitácora y exportaciones)
docker compose exec web python manage.py test

# Motor de autorización (contenedor aparte, sin Django ni base)
docker compose exec autorizador python -m unittest
```

Las pruebas de `autorizacion/` no las descubre `manage.py test` sin etiquetas; para correr todo:

```bash
docker compose exec web sh -c 'python manage.py test . $(ls autorizacion/test_*.py autorizacion/tests.py | sed "s#/#.#; s#[.]py##")'
```

## 8. Problemas comunes

- **Todo da 403/409 y el motor no responde**: revise `docker compose ps` y
  `docker compose logs autorizador`. El sistema falla cerrado si el autorizador no está sano.
- **No puedo entrar**: confirme que corrió `crear_usuarios_prueba`, que copió la contraseña sin
  espacios accidentales y que el reloj de la máquina está sincronizado (el TOTP es basado en tiempo).
- **El TOTP dice que el código ya se usó**: espere al siguiente código de 30 segundos.
- **Windows bloquea `psycopg`**: use Docker; no instale Python/PostgreSQL en el host para este
  proyecto.
- **Quiero empezar de cero**: `docker compose down -v` borra el volumen de PostgreSQL local.
