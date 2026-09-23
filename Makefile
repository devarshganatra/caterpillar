.PHONY: up down build logs shell test reset gen-data gen-data-dev gen-data-tiny test-ml train-eta train-anomaly baselines evaluate train

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

# Stage 3 / Batch 3A — synthetic historical dataset generation.
# "full" (60 days, ~50k windows) is what ml/artifacts/ models are trained on;
# "dev"/"tiny" are faster profiles for local iteration (see ml/generate_history.py PROFILES).
gen-data:
	PYTHONPATH=. venv/bin/python -m ml.generate_history --profile full --seed 42 --out ml/data

gen-data-dev:
	PYTHONPATH=. venv/bin/python -m ml.generate_history --profile dev --seed 42 --out ml/data

gen-data-tiny:
	PYTHONPATH=. venv/bin/python -m ml.generate_history --profile tiny --seed 42 --out ml/data

test-ml:
	PYTHONPATH=. venv/bin/python -m pytest ml/tests/ -v

train-eta:
	PYTHONPATH=. venv/bin/python -m ml.train_eta --data ml/data --out ml/artifacts --seed 42

baselines:
	PYTHONPATH=. venv/bin/python -m ml.baselines --data ml/data --out ml/artifacts

train-anomaly:
	PYTHONPATH=. venv/bin/python -m ml.train_anomaly --data ml/data --out ml/artifacts --seed 42

evaluate:
	PYTHONPATH=. venv/bin/python -m ml.evaluate --data ml/data --artifacts ml/artifacts

# Full pipeline per BUILD_PLAN_new.md section 7: generate -> train -> evaluate.
train: gen-data train-eta baselines train-anomaly evaluate
