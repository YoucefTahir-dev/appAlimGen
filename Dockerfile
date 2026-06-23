FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE gestio_stock.settings

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential libpq-dev gcc libssl-dev libjpeg-dev zlib1g-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput

CMD ["gunicorn", "gestio_stock.wsgi:application", "--bind", "0.0.0.0:8000"]
