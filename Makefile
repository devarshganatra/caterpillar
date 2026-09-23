.PHONY: up down build logs shell test reset

up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build

logs:
	docker-compose logs -f

shell:
	docker-compose exec api /bin/bash

test:
	python -m pytest core/ -v

reset:
	docker-compose down -v
	docker-compose up -d
