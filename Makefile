SHELL := /bin/bash

.PHONY: dev up down logs ps fmt lint test

dev:
	docker compose -f backend/docker-compose.yml up --build

up:
	docker compose -f backend/docker-compose.yml up -d --build

down:
	docker compose -f backend/docker-compose.yml down -v

logs:
	docker compose -f backend/docker-compose.yml logs -f --tail=200

ps:
	docker compose -f backend/docker-compose.yml ps

fmt:
	docker compose -f backend/docker-compose.yml run --rm api ruff format .
	docker compose -f backend/docker-compose.yml run --rm api ruff check --fix .

lint:
	docker compose -f backend/docker-compose.yml run --rm api ruff check .

test:
	docker compose -f backend/docker-compose.yml run --rm api pytest -q
