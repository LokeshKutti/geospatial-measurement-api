FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 DJANGO_DEBUG=0 DJANGO_DB_PATH=/data/db.sqlite3 DJANGO_MEDIA_ROOT=/data/media
WORKDIR /app
# pyogrio/pyproj/shapely ship manylinux wheels that bundle GDAL/PROJ/GEOS: no apt packages needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application -b 0.0.0.0:8000 --timeout 120"]
