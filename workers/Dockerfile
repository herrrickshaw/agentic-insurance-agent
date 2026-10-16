FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir numpy pydantic
COPY workers/*.py ./
COPY insurance ./insurance
ENV LEDGER_DB=/data/ledger.db PYTHONUNBUFFERED=1
CMD ["python", "runner.py"]
