BACKEND := backend
PY := $(BACKEND)/.venv/bin

.PHONY: install migrate revision seed run test lint fmt clean

install:
	cd $(BACKEND) && uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"
	@test -f $(BACKEND)/.env || cp $(BACKEND)/.env.example $(BACKEND)/.env

migrate:
	cd $(BACKEND) && .venv/bin/alembic upgrade head

revision:
	cd $(BACKEND) && .venv/bin/alembic revision --autogenerate -m "$(m)"

seed:
	cd $(BACKEND) && .venv/bin/python -m scripts.seed_demo

run:
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload

test:
	cd $(BACKEND) && .venv/bin/pytest

lint:
	cd $(BACKEND) && .venv/bin/ruff check .

fmt:
	cd $(BACKEND) && .venv/bin/ruff check --fix . && .venv/bin/ruff format .

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache
