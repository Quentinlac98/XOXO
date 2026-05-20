.PHONY: up down logs shell build restart

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

restart:
	docker compose restart

logs:
	docker compose logs -f

shell:
	docker compose exec xoxo bash
