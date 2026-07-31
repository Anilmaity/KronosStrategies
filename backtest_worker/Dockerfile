# Manager Backtest worker image.
#
# Build context is the REPO ROOT (see compose.yml backtest_worker.build):
# unlike the runner services, this worker imports BOTH the strategies tree
# (shared.*, backtest.*, backtest_strategies.*, research.*, audit_worker.*)
# and the strategy_manager package (policies, regime) — the latter does not
# exist inside the ./strategies build context. Layout mirrors how tests run
# from the repo root: /app = strategies/*, plus /app/strategy_manager.
#
# Context filtering lives in Dockerfile.dockerignore next to this file
# (BuildKit per-Dockerfile ignore) so the root context stays small.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY backtest_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY strategies/ /app/
COPY strategy_manager/ /app/strategy_manager/

CMD ["python", "-m", "audit_worker.worker"]
