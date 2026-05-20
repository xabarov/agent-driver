PYTHON ?= ../../../.venv/bin/python
UVICORN ?= uvicorn
APP_HOST ?= 127.0.0.1
# 8000 is often taken (e.g. Zion/docker). Override: make dev-full APP_PORT=8020
APP_PORT ?= 8010
API_PROXY_TARGET ?= http://$(APP_HOST):$(APP_PORT)

.PHONY: install install-backend install-frontend dev dev-frontend dev-full test test-frontend lint docker-up build-frontend build

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
	@if command -v ss >/dev/null 2>&1 && ss -tln | grep -q ":$(APP_PORT) "; then \
		echo "ERROR: port $(APP_PORT) is already in use."; \
		echo "Try: make dev-full APP_PORT=8020"; \
		exit 1; \
	fi
	@echo "Backend: http://$(APP_HOST):$(APP_PORT)  Frontend: http://127.0.0.1:5173  (proxy /api -> $(API_PROXY_TARGET))"
	@trap 'kill 0' INT TERM EXIT; \
	(cd backend && . .venv/bin/activate && $(UVICORN) app.main:create_app --factory --reload --host $(APP_HOST) --port $(APP_PORT)) & \
	(cd frontend && VITE_API_PROXY_TARGET="$(API_PROXY_TARGET)" npx pnpm dev --host 127.0.0.1 --port 5173) & \
	wait

test:
	cd backend && . .venv/bin/activate && pytest -q

test-frontend:
	cd frontend && npx pnpm test --run

build-frontend:
	cd frontend && npx pnpm build

build: build-frontend
	rm -rf backend/app/static
	cp -r frontend/dist backend/app/static

lint:
	cd backend && . .venv/bin/activate && ruff check app tests

docker-up:
	docker compose up --build
