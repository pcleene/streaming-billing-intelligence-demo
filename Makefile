.PHONY: help install seed seed-small teardown teardown-full reseed reseed-small \
        dev backend frontend simulator features rules-watcher \
        metrics-recorder drift-detector burst asp-deploy asp-stop topic-create \
        demo spark-stream screenshots clean

# --- Burst-mode knobs (override on the command line) -----------------
# Example:  make burst BURST_TPS=300 BURST_DURATION=600
BURST_TPS      ?= 200
BURST_DURATION ?= 300
BURST_RAMP     ?= 30

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip
VENV   ?= .venv
ACTIVATE = . $(VENV)/bin/activate

help:
	@echo "Streaming Billing — demo targets"
	@echo "  make install          Create venv + install backend + data-generator deps; install frontend deps"
	@echo "  make topic-create     Create the MSK topic (one-time)"
	@echo "  make seed             Seed full demo dataset (10k customers + 500 history) into Atlas"
	@echo "  make seed-small       Seed a small dataset (50 customers, 20 history) for quick smoke tests"
	@echo "  make teardown         Empty all streaming_billing collections (delete_many; keeps schema)"
	@echo "  make teardown-full    Drop collections + Atlas Search/Vector indexes (clean slate)"
	@echo "  make reseed           teardown + seed (full)"
	@echo "  make reseed-small     teardown + seed-small"
	@echo "  make asp-deploy       Deploy all Atlas Stream Processing pipelines via mongosh"
	@echo "  make asp-stop         Stop all Acme ASP processors"
	@echo "  make backend          Run FastAPI backend (uvicorn)"
	@echo "  make frontend         Run SvelteKit dev server"
	@echo "  make simulator        Run transaction simulator → MSK"
	@echo "  make burst            Run a one-shot end-of-month TPS burst (BURST_TPS / BURST_DURATION / BURST_RAMP)"
	@echo "  make features         Run feature engineering worker"
	@echo "  make rules-watcher    Run rule change-stream watcher"
	@echo "  make metrics-recorder Sample TPS / p99 / queue-depth into system_metrics"
	@echo "  make drift-detector   KS-test feature drift every 15m → feature_drift_metrics"
	@echo "  make demo             Bring up the full demo (compose up + simulator)"
	@echo "  make spark-stream     PySpark structured streaming → Delta + Mongo (see ml/jobs/stream_features_live.py)"
	@echo "  make screenshots      Capture demo screenshots via Playwright"
	@echo "  make clean            Remove caches and build artefacts"

install:
	$(PYTHON) -m venv $(VENV)
	$(ACTIVATE) && $(PIP) install --upgrade pip
	$(ACTIVATE) && $(PIP) install -e backend
	$(ACTIVATE) && $(PIP) install -e data-generator
	cd frontend && npm install

topic-create:
	$(ACTIVATE) && $(PYTHON) -m app.streaming.topic_admin create

seed:
	cd backend && $(ACTIVATE) && $(PYTHON) -m scripts.seed

# A small dataset suitable for fast smoke-tests / first-time bring-up.
# AutoEmbed (ADR-032): history docs carry only `embed_source.text` —
# Atlas owns the vector — so no Voyage cost is incurred at seed time.
seed-small:
	cd backend && $(ACTIVATE) && $(PYTHON) -m scripts.seed \
	    --customers 50 \
	    --target-txns 200 \
	    --max-txns-per-customer 12 \
	    --history 20 \
	    --quarantine-cases 8 \
	    --commercial-parents 2 \
	    --feature-docs 30 \
	    --burst-samples 20 \
	    --steady-samples 10

# Empty all collections (keeps validators + indexes). Idempotent.
teardown:
	cd backend && $(ACTIVATE) && $(PYTHON) -m scripts.teardown --yes

# Drop collections AND Atlas Search/Vector Search indexes. Use this when
# you intend to change schemas or index definitions before re-seeding.
teardown-full:
	cd backend && $(ACTIVATE) && $(PYTHON) -m scripts.teardown --full --yes

# Convenience composites — both call the two underlying commands separately.
reseed: teardown seed

reseed-small: teardown seed-small

asp-deploy:
	mongosh "$$ASP_URI" --file infra/asp/deploy_all.js

asp-stop:
	mongosh "$$ASP_URI" --file infra/asp/stop_all.js

backend:
	cd backend && $(ACTIVATE) && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && npm run dev

simulator:
	$(ACTIVATE) && $(PYTHON) -m app.workers.transaction_simulator

burst:
	$(ACTIVATE) && $(PYTHON) -m app.workers.transaction_simulator \
	    --burst \
	    --burst-target-tps $(BURST_TPS) \
	    --burst-duration-seconds $(BURST_DURATION) \
	    --burst-ramp-seconds $(BURST_RAMP)

features:
	$(ACTIVATE) && $(PYTHON) -m app.workers.feature_engineer

rules-watcher:
	$(ACTIVATE) && $(PYTHON) -m app.workers.rule_change_watcher

metrics-recorder:
	$(ACTIVATE) && $(PYTHON) -m app.workers.metrics_recorder

drift-detector:
	$(ACTIVATE) && $(PYTHON) -m app.workers.feature_drift_detector

demo:
	docker compose up -d
	@echo "Backend: http://localhost:8000  |  Frontend: http://localhost:5173"
	@echo "Run 'make seed' in another terminal if this is a fresh cluster."

# Spark streaming (Option A): requires pyspark + delta-spark in .venv (see ml/jobs/stream_features_live.py).
# `exec` makes python the foreground process so Ctrl+C / SIGTERM reach it directly.
spark-stream:
	$(ACTIVATE) && exec $(PYTHON) ml/jobs/stream_features_live.py

screenshots:
	cd frontend && npx playwright test --project=chromium tests/screenshots.spec.ts

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	rm -rf frontend/.svelte-kit frontend/build
