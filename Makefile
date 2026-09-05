.PHONY: up down migrate seed seed-deals seed-history seed-all test test-unit test-integration lint clean

up:
	docker compose up -d

down:
	docker compose down

migrate:
	alembic upgrade head

seed:
	python -m app.seed.seed seed

seed-deals:
	python -m app.seed.seed seed-deals

seed-history:
	python -m app.seed.seed seed-history

seed-all:
	python -m app.seed.seed all

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

lint:
	flake8 app/ tests/ || true

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
