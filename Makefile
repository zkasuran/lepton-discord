.PHONY: install dev-install lint test bot api

install:
	uv venv && uv pip install -e .

dev-install:
	uv venv && uv pip install -e ".[dev]"

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/
	uv run mypy src/

test:
	uv run pytest -q

bot:
	uv run python run_bot.py

api:
	uv run python run_api.py

api-dev:
	uv run uvicorn src.api.app:app --host 0.0.0.0 --port 8402 --reload

fmt:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/
