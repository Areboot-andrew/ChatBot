# Увага: використовуємо python:3.12-slim
FROM python:3.12-slim AS builder

WORKDIR /app

# Встановлюємо залежності системи, необхідні для збірки
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Копіюємо requirements і встановлюємо
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# --- Runtime stage ---
FROM python:3.12-slim

WORKDIR /app

# Встановлюємо runtime залежності
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Копіюємо колеса і встановлюємо
COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache /wheels/*

# Копіюємо код
COPY ./app /app/app
COPY ./alembic /app/alembic
COPY ./alembic.ini /app/alembic.ini
COPY ./knowledge_template.yaml /app/knowledge_template.yaml

# Для gateway
EXPOSE 8000

CMD sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"
