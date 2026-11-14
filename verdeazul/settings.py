"""
Configuración de Django para la plataforma de Verdeazul Carbono S.A.

Ningún secreto vive en este archivo: todo lo sensible se lee de variables de
entorno. En Cloud Run, Secret Manager las inyecta al contenedor; en desarrollo
local se leen de un archivo .env (ignorado por git, ver .env.example).
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# En local carga .env si existe; en Cloud Run no hay .env y las variables
# vienen de Secret Manager. Nunca sobrescribe variables ya definidas.
load_dotenv(BASE_DIR / ".env", override=False)


def env_obligatoria(nombre: str) -> str:
    valor = os.environ.get(nombre)
    if not valor:
        raise ImproperlyConfigured(f"Falta la variable de entorno obligatoria {nombre}.")
    return valor


def env_bool(nombre: str, defecto: bool = False) -> bool:
    return os.environ.get(nombre, str(defecto)).strip().lower() in {"1", "true", "yes", "si", "sí"}


def env_lista(nombre: str) -> list[str]:
    return [x.strip() for x in os.environ.get(nombre, "").split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Núcleo
# ---------------------------------------------------------------------------
SECRET_KEY = env_obligatoria("SECRET_KEY")
DEBUG = env_bool("DEBUG", False)  # por defecto apagado: fallo seguro
ALLOWED_HOSTS = env_lista("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_lista("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Apps del proyecto
    "cuentas",
    "catalogo",
    "mediciones",
]

# Modelo de usuario propio: fijado desde la primera migración (no se puede cambiar después).
AUTH_USER_MODEL = "cuentas.Usuario"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "verdeazul.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "verdeazul.wsgi.application"

# ---------------------------------------------------------------------------
# Base de datos: PostgreSQL (Docker en local, Neon en producción)
# ---------------------------------------------------------------------------
# Fuera de DEBUG se exige TLS hacia la base (Neon: sslmode=require).
DATABASES = {
    "default": dj_database_url.parse(
        env_obligatoria("DATABASE_URL"),
        conn_max_age=60,
        conn_health_checks=True,
        ssl_require=not DEBUG,
    )
}

# ---------------------------------------------------------------------------
# Contraseñas: Argon2id primero (hash lento con sal), mínimo 12 caracteres
# (Políticas §6.1; Arquitectura §4.3)
# ---------------------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",  # solo para verificar hashes antiguos
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Sesión y cookies (Arquitectura §4.4). Persona 3 completa inactividad por rol.
# ---------------------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.db"  # del lado del servidor, revocable
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 10 * 60 * 60  # máximo absoluto de 10 horas
CSRF_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# HTTPS: solo fuera de DEBUG (Cloud Run termina TLS y reenvía por HTTP interno)
# ---------------------------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_NAME = "__Host-vz_sesion"
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# Internacionalización: hora del servidor en UTC; Costa Rica para mostrar
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "es"
TIME_ZONE = "America/Costa_Rica"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Puntos de enganche entre equipos (ver nucleo/ganchos.py y README "Contrato").
# Persona 3 y Persona 4 agregan aquí la ruta de su implementación.
# ---------------------------------------------------------------------------
VERDEAZUL_GANCHOS = {
    # "autorizar": "autorizacion.motor.autorizar",
    "usuarios_con_rol": "cuentas.roles.usuarios_con_rol",
    # "registrar_evento": "bitacora.servicios.registrar_evento",
    # "on_medicion_aprobada": "certificados.servicios.emitir",
}
# Sin motor de autorización configurado se NIEGA todo (fallo cerrado), excepto en
# desarrollo local con DEBUG, para poder probar el flujo mientras Persona 3 termina.
AUTORIZACION_PROVISIONAL_PERMISIVA = DEBUG

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"consola": {"class": "logging.StreamHandler"}},
    "loggers": {"verdeazul": {"handlers": ["consola"], "level": "INFO"}},
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
