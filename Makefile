# Удобства для разработки; README работает и без make.
.PHONY: data up down logs emulator test lint train submit mock docs-site

data:            ## распаковать датасет из ./data/*.zip в ./data/raw
	uv run python -m scripts.data_prep --with-emulator

up:              ## поднять систему
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

emulator:        ## загрузить образ эмулятора и поднять его с конфигом
	docker load -i data/raw/ndtp-telemetry-emulator.tar
	docker compose --profile emulator up -d

test:
	uv run pytest -q

lint:
	uv run ruff check

train:           ## CV + обучение моделей в ./models
	uv run python -m services.ml.app.train

submit:          ## сабмит по validate + проверка формата
	uv run python -m scripts.make_submission

mock:            ## mock-бэкенд для фронта на :8000
	node contracts/mock-server/server.mjs

docs-site:       ## статический сайт документации (Sphinx + Swagger) в ./site
	uv run python -m scripts.build_docs_site
