PYTHON ?= ../../../.venv/bin/python
UVICORN ?= uvicorn
APP_HOST ?= 127.0.0.1
APP_PORT ?= 8000

.PHONY: install install-backend install-frontend dev dev-frontend dev-full test test-frontend lint docker-up build-frontend

install: install-backend install-frontend

install-backend:
	cd backend && rm -rf .venv && if [ -x "$(PYTHON)" ]; then $(PYTHON) -m venv .venv; else python3 -m venv .venv; fi
	cd backend && . .venv/bin/activate && pip install -U pip
	cd backend && . .venv/bin/activate && pip install -e ../../..[cli]
	cd backend && . .venv/bin/activate && pip install -e .[dev]

install-frontend:
	cd frontend && npx pnpm install

dev:
	cd backend && . .venv/bin/activate && $(UVICORN) app.main:create_app --factory --reload --host $(APP_HOST) --port $(APP_PORT)

dev-frontend:
	cd frontend && npx pnpm dev --host 127.0.0.1 --port 5173

dev-full:
	@echo "Use two terminals:"
	@echo "1) make dev"
	@echo "2) make dev-frontend"

test:
	cd backend && . .venv/bin/activate && pytest -q

test-frontend:
	cd frontend && npx pnpm test --run

build-frontend:
	cd frontend && npx pnpm build

lint:
	cd backend && . .venv/bin/activate && ruff check app tests

docker-up:
	docker compose up --build
