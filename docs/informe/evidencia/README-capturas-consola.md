# Capturas de consola pendientes (GCP y GitHub)

Estas capturas **no se pueden generar desde el repositorio**: requieren la sesión del
navegador con la cuenta que administra el proyecto de GCP y el repositorio de GitHub.
No se inventan ni se reconstruyen: el informe (`docs/informe/P2_Informe_Implementacion_Grupo5.tex`,
línea 434) afirma que no se verificaron en consola, así que la evidencia debe ser real.

Proyecto: `project-4d85c806-d471-4964-b73` · Región: `us-east1` · Repo: `Guzroj/Proyecto2_Seguridad`

Convención de nombres para los archivos (van a `docs/informe/evidencia/consola/`):

| Archivo | Contenido |
|---|---|
| `gcp-01-secret-manager-lista.png` | Los siete secretos |
| `gcp-02-secret-manager-permisos.png` | IAM de `verdeazul-certificado-clave` |
| `gcp-03-artifact-registry-app.png` | Etiquetas de `app` |
| `gcp-04-artifact-registry-autorizador.png` | Etiquetas de `autorizador` |
| `gcp-05-cloud-run-servicios.png` | Servicios y revisión en ejecución |
| `github-01-code-security.png` | Dependabot activo |
| `github-02-dependabot-alerts.png` | Alertas atendidas |
| `github-03-actions-deploy.png` | Run de `deploy.yml` |
| `github-04-ramas-dependabot.png` | Ramas del bot y PR #9 |

---

## GCP · Secret Manager (2 capturas)

**Lista de secretos** — https://console.cloud.google.com/security/secret-manager?project=project-4d85c806-d471-4964-b73

- Deben verse los siete nombres de la tabla del informe:
  `verdeazul-secret-key`, `verdeazul-db-url`, `verdeazul-migrate-db-url`,
  `verdeazul-mfa-clave-cifrado`, `verdeazul-certificado-clave`,
  `verdeazul-allowed-hosts`, `verdeazul-csrf-origins`.
- **No abras "View secret value" de ningún secreto.** La lista no muestra valores; no
  hagas clic en un secreto para no arriesgar un clic de más.
- Revisa que en la captura no aparezca el correo personal de nadie más de lo necesario
  (los roles sí deben verse).

**Permisos del secreto de firma** — Secret Manager → clic en `verdeazul-certificado-clave` → pestaña **PERMISSIONS**

- Debe aparecer `verdeazul-app@project-4d85c806-d471-4964-b73.iam.gserviceaccount.com`
  con rol **Secret Manager Secret Accessor**.
- Debe **no** aparecer `verdeazul-autorizador@...` (el autorizador no recibe secretos).
- No entres a la pestaña **VERSIONS** para no exponer metadatos innecesarios, y nunca
  a "View secret value".

## GCP · Artifact Registry (2 capturas)

https://console.cloud.google.com/artifacts/docker/project-4d85c806-d471-4964-b73/us-east1/verdeazul

- Imagen `app` → pestaña **Tags**: deben verse una versión semántica `vX.Y.Z` (p. ej. `v1.1.0`)
  y al menos una `sha-<commit>`.
- Imagen `autorizador` → lo mismo.
- Si la lista es larga, filtra por `v` y por `sha-` en vez de recortar.
- Alternativa válida si la consola no carga bien: `gcloud artifacts docker tags list` (ver
  `capturar_consola.sh`), que produce salida de texto como la de `https.txt`.

## GCP · Cloud Run (1 captura, opcional pero recomendada)

https://console.cloud.google.com/run?project=project-4d85c806-d471-4964-b73

- Servicios `verdeazul` y `verdeazul-autorizador`, con la **revisión** y la **URL**.
- Útil porque el informe dice que la revisión en ejecución era `manual-202611121557` y no
  correspondía a `develop`: esta captura demuestra el estado real tras el merge.

---

## GitHub · Code security (1 captura)

`Settings` → `Code security` (https://github.com/Guzroj/Proyecto2_Seguridad/settings/security_analysis)

- **Dependabot alerts**: Enabled.
- **Dependabot security updates**: Enabled.
- Se corresponde con `.github/dependabot.yml`, que revisa pip y Docker cada semana hacia `develop`.

## GitHub · Dependabot alerts (1 captura)

`Security` → `Dependabot alerts` (https://github.com/Guzroj/Proyecto2_Seguridad/security/dependabot)

- Debe constar la alerta **atendida**: el texto del informe menciona que se atendieron las
  dos primeras (Python 3.15-slim y Django 6.1.3) en el PR #9 y se agregaron reglas `ignore`
  porque rompen Django 5.2 LTS y Python 3.12.
- Las cuatro menores del 17-nov siguen abiertas: eso **también** es correcto y el informe lo
  dice. No hace falta "limpiarlas" para la captura; si se capturan abiertas, ajusta la frase
  del informe o aclara en el pie de figura.

## GitHub · Actions (1 captura)

`Actions` → workflow **deploy.yml** (https://github.com/Guzroj/Proyecto2_Seguridad/actions/workflows/deploy.yml)

- Un run verde sobre `main`, con los tres jobs: build-and-push, migrate y deploy.
- Anota en el pie la etiqueta `sha-...` publicada; debe coincidir con la de Artifact Registry.

## GitHub · Ramas de Dependabot (1 captura)

https://github.com/Guzroj/Proyecto2_Seguridad/branches

- Las seis ramas `dependabot/...` y, si se quiere, el PR #9 cerrado.

---

## Estado de la entrega verificado el 20-nov-2026 (03:00 UTC)

**El merge pendiente ya se hizo:** PR #18 `develop` → `main`, commit `53e5b7f`, desplegado.
La imagen en ejecución es `app:sha-53e5b7f` (revisión `verdeazul-00005-rxg`) y el autorizador
`autorizador:sha-53e5b7f` (revisión `verdeazul-autorizador-00003-wg8`). `origin/main` y
`origin/develop` apuntan al mismo árbol.

- `/cuentas/login/`, `/certificados/verificar/` y `/certificados/clave-publica/` responden **200**.
- La revisión viva referencia los **siete** secretos, incluidos `MFA_CLAVE_CIFRADO` y
  `CERTIFICADO_CLAVE_PRIVADA` (`gcp-06`).
- **Huella de producción comprobada** (`gcp-07`): `/certificados/clave-publica/` devuelve
  `14428e8d992fa57dfb65003add2e7332a1c2372b5bd584b7b942fec7dba907c2`, la misma que
  `CERTIFICADO_CLAVES_CONFIABLES` en `deploy.yml`. Esto cierra el **PENDIENTE** del
  `informe-implementacion.md` (líneas 162-163).
- `v1.0.0` ya apunta al digest de `sha-53e5b7f` en ambas imágenes, es decir, la etiqueta
  corresponde a la versión desplegada.

### Hallazgos de la corrida anterior (01:55 UTC)

Recolectado con `capturar_consola.sh`; salidas completas en `consola/*.txt`.
Los cuatro últimos puntos **ya están resueltos**; se conservan porque explican qué cambió.

1. **Secretos:** existen exactamente los siete de la tabla, sin faltantes ni sobrantes
   (`verdeazul-certificado-clave` se creó el 18-nov, el resto el 12/16-nov).
2. **Permisos IAM reales (coinciden con el informe):**
   - `verdeazul-app`: `secret-key`, `db-url`, `mfa-clave-cifrado`, `allowed-hosts`,
     `csrf-origins`, `certificado-clave`. **No** tiene `migrate-db-url`.
   - `verdeazul-migrate`: solo `secret-key` y `migrate-db-url`.
   - `verdeazul-autorizador`: **ningún** secreto.
   - `verdeazul-deploy`: los siete (es la SA del pipeline).
   - Excepción a documentar: dos personas tienen `roles/owner` en el proyecto
     (`urizandres`, `marianarojas`) y Owner puede leer cualquier secreto, así que el
     mínimo privilegio vale **entre service accounts**, no frente a administradores.
3. **Artifact Registry [resuelto]:** entonces `v1.0.0` apuntaba a `sha-6ce4cad` (13-nov).
   Hoy `v1.0.0` apunta a `sha-53e5b7f`. Queda la etiqueta histórica
   `manual-202611121557`: conviene explicarla como despliegue manual previo al pipeline.
4. **`verdeazul-auth` [resuelto]:** era un tercer servicio de Cloud Run con la imagen de
   ejemplo `container/hello` y la SA por defecto de Compute, ajeno a `deploy.yml` y al
   informe. **Ya fue eliminado**; no aparece en `gcp-05`.
5. **Secretos de la revisión [resuelto]:** la revisión vieja solo referenciaba cuatro
   secretos; la actual referencia los siete (`gcp-06`).
6. **URL pública [resuelto]:** antes daba 404 porque corría la revisión de `main` previa
   a `develop`. Hoy responde 200. Las cabeceras siguen bien: HSTS
   (`max-age=31536000; includeSubDomains`), `X-Frame-Options: DENY`,
   `X-Content-Type-Options: nosniff`.

## Reglas para no filtrar nada

1. Nunca capturar valores de secretos, `DATABASE_URL`, contraseñas ni claves.
2. No capturar la barra de direcciones con tokens de sesión ni correos ajenos.
3. Recortar solo lo que no aporta (menús laterales vacíos); no editar el contenido de la
   evidencia. Si recortas, márcalo como `[…]` igual que en las figuras del informe.
4. Guardar en PNG a ancho completo y con la fecha visible si la interfaz la muestra.
