"""Long-polling external-task worker for Operaton / Camunda 7 REST (/engine-rest). Stdlib only."""
import json
import logging
import os
import time
import urllib.request

from handlers import HANDLERS
from ledger import connect

ENGINE = os.environ.get("ENGINE_URL", "http://localhost:8080/engine-rest")
WORKER_ID = os.environ.get("WORKER_ID", "py-worker-1")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def call(path, body):
    req = urllib.request.Request(f"{ENGINE}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return json.loads(data) if data else None


def typed(value):
    if isinstance(value, bool):
        return {"value": value, "type": "Boolean"}
    if isinstance(value, int):
        return {"value": value, "type": "Long"}
    if isinstance(value, float):
        return {"value": value, "type": "Double"}
    return {"value": value, "type": "String"}


def main():
    con = connect()
    topics = [{"topicName": t, "lockDuration": 30000, "deserializeValues": False} for t in HANDLERS]
    logging.info("worker up, topics=%s", list(HANDLERS))
    while True:
        try:
            tasks = call("/external-task/fetchAndLock", {"workerId": WORKER_ID, "maxTasks": 10,
                                                         "asyncResponseTimeout": 20000, "topics": topics})
        except Exception as e:  # engine not up yet
            logging.warning("engine unavailable: %s", e)
            time.sleep(3)
            continue
        for t in tasks:
            vars_ = {k: v.get("value") for k, v in t.get("variables", {}).items()}
            try:
                out = HANDLERS[t["topicName"]](con, vars_)
                call(f"/external-task/{t['id']}/complete",
                     {"workerId": WORKER_ID, "variables": {k: typed(v) for k, v in out.items()}})
                logging.info("done %s (%s)", t["topicName"], t["processInstanceId"][:8])
            except Exception as e:
                logging.exception("failed %s", t["topicName"])
                retries = t.get("retries") if t.get("retries") is not None else 3
                call(f"/external-task/{t['id']}/failure", {"workerId": WORKER_ID, "errorMessage": str(e)[:500],
                                                          "retries": retries - 1, "retryTimeout": 5000})


if __name__ == "__main__":
    main()
