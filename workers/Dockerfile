FROM python:3.12-slim
WORKDIR /app
COPY *.py ./
ENV LEDGER_DB=/data/ledger.db PYTHONUNBUFFERED=1
CMD ["python", "runner.py"]
