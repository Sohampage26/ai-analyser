FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd -m -u 1000 jaa && mkdir -p /data && chown jaa /data

USER jaa
EXPOSE 8000 8001
CMD ["uvicorn", "analyser.api:app", "--host", "0.0.0.0", "--port", "8000"]
