.PHONY: bootstrap dev test lint typecheck build config up down logs ps migrate smoke smoke-powershell clean reset-data

.env:
	cp .env.example .env
	@echo "Created .env from placeholders. Rotate every credential before non-local use."

bootstrap: .env
	npm install
	uv sync --project backend --all-groups

dev:
	npm run dev

test:
	npm test
	uv run --project backend pytest -c backend/pyproject.toml backend/tests

lint:
	npm run lint
	uv run --project backend ruff check backend

typecheck:
	npm run typecheck
	uv run --project backend mypy --config-file backend/pyproject.toml backend/src

build:
	npm run build
	docker compose build

config:
	docker compose config --quiet

up: .env
	docker compose up --build -d
	@echo "Northstar is starting at http://localhost:3000. Run 'make smoke' to wait for readiness."

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

migrate:
	docker compose run --rm migrate

smoke:
	bash scripts/smoke.sh

smoke-powershell:
	pwsh -NoProfile -File scripts/smoke.ps1

clean:
	docker compose down --remove-orphans

reset-data:
	docker compose down --volumes --remove-orphans
