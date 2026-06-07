LANGGRAPH_WORKERS ?= 3

install:
	uv sync

dev:
	BG_JOB_ISOLATED_LOOPS=true uv run langgraph dev --no-browser --allow-blocking --no-reload --n-jobs-per-worker $(LANGGRAPH_WORKERS)

gateway:
	uv run uvicorn src.gateway.app:app --host 0.0.0.0 --port 8001

test:
	PYTHONPATH=. uv run pytest tests/ -v

lint:
	uvx ruff check .

format:
	uvx ruff check . --fix && uvx ruff format .

tools-audit:
	PYTHONPATH=. uv run python -m src.tools.audit $(ARGS)
