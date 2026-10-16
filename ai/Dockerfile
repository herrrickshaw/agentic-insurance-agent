FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic httpx "psycopg[binary]"
COPY ai ./ai
ENV PYTHONUNBUFFERED=1
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8090/health')"
CMD ["uvicorn", "ai.service:app", "--host", "0.0.0.0", "--port", "8090"]
