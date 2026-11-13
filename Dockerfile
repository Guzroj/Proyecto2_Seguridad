# Imagen de la aplicación Django (Verdeazul).
# Misma imagen para desarrollo local (docker compose) y Cloud Run.
FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Usuario sin privilegios: el contenedor NO corre como root (mínimo privilegio).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

USER app

# Cloud Run define PORT (8080 por defecto).
EXPOSE 8080
CMD ["sh", "-c", "exec gunicorn verdeazul.wsgi:application --bind 0.0.0.0:${PORT:-8080} --workers 2 --access-logfile -"]
