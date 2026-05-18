# ============================================================
#  GOSSIP GIRL PARTY — Dockerfile
#  Cible : Synology DS923+ (linux/amd64)
# ============================================================

FROM python:3.11-slim

# Dépendances système pour Pillow (JPEG/PNG) et qrcode
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        libpng16-16 \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installer les dépendances Python en premier (layer caché si requirements inchangé)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code source
COPY app/        ./app/
COPY templates/  ./templates/
COPY static/     ./static/
COPY questions/  ./questions/

# Créer les répertoires de données persistants
RUN mkdir -p data/db data/uploads

# Exposer le port applicatif
EXPOSE 7777

# Healthcheck : ping l'endpoint /health toutes les 30s
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7777/health')"

# Lancement via le module __main__ (eventlet.monkey_patch en premier)
CMD ["python", "-m", "app.__main__"]
