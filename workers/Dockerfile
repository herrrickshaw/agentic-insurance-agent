FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir numpy pydantic httpx
COPY workers/*.py ./
COPY insurance ./insurance
COPY ai ./ai
COPY records ./records
ENV LEDGER_DB=/data/ledger.db PYTHONUNBUFFERED=1 RECORDS_OUT=/data/out
CMD ["python", "runner.py"]
