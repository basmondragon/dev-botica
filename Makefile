.PHONY: setup up down db migrate rls test seed platform-admin schema check check-server format format-check web

-include .env

DJANGO_SECRET_KEY ?= dev
BOTICA_MIGRATION_USER ?= botica_migrator
BOTICA_MIGRATION_PASSWORD ?= devmigratorpw
BOTICA_RUNTIME_USER ?= botica_app
BOTICA_RUNTIME_PASSWORD ?= devapppw
export

DB_ENV = DJANGO_SETTINGS_MODULE=botica.settings POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
PY = .venv/bin/python

setup:
	test -f .env || cp .env.example .env
	uv venv --python 3.13 .venv
	uv pip install --python $(PY) -e ".[dev]"
	npm --prefix web install

up:
	docker compose up -d --build

down:
	docker compose down

db:
	docker compose up -d postgres

migrate:
	$(DB_ENV) BOTICA_DB_ROLE=migration $(PY) manage.py migrate

rls:
	$(DB_ENV) $(PY) manage.py check_rls

PYTEST ?= core/tests

# `--create-db` clobbers any test database a previous interrupted run left
# behind. Without it, a half-torn-down database collides with creation and the
# suite errors on whichever test happens to be first.
test:
	$(DB_ENV) BOTICA_DB_ROLE=migration $(PY) -m pytest $(PYTEST) -q --create-db

seed:
	$(DB_ENV) $(PY) manage.py seed_demo_tenant --profile $(PROFILE)

platform-admin:
	$(DB_ENV) BOTICA_DB_ROLE=migration $(PY) manage.py create_platform_admin \
	  --email $(EMAIL) --name "$(NAME)"

schema:
	$(DB_ENV) $(PY) manage.py export_openapi
	npm --prefix web run generate:api

check-server:
	$(DB_ENV) $(PY) manage.py check_rls
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(DB_ENV) $(PY) -m mypy core botica

check: check-server
	npm --prefix web run typecheck
	npm --prefix web run lint
	npm --prefix web run format:check
	npm --prefix web run conformance
	npm --prefix web run test
	$(DB_ENV) $(PY) manage.py export_openapi --out /tmp/botica-openapi.json
	diff -q /tmp/botica-openapi.json schema/openapi.json \
	  || (echo "schema/openapi.json is stale — run 'make schema'"; exit 1)

format:
	$(PY) -m ruff format .
	npm --prefix web run format

format-check:
	$(PY) -m ruff format --check .
	npm --prefix web run format:check

web:
	npm --prefix web run dev
