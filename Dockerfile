FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE gestio_stock.settings

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential libpq-dev postgresql-client gcc libssl-dev libjpeg-dev zlib1g-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

RUN groupadd --system erp && useradd --system --gid erp --home-dir /app erp

COPY --chown=erp:erp . .

RUN DJANGO_DEBUG=True python manage.py collectstatic --noinput

USER erp

CMD ["gunicorn", "gestio_stock.wsgi:application", "--bind", "0.0.0.0:8000"]
