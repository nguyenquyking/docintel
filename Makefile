# DocIntel — common tasks. Requires uv (Python) and Node.js ≥ 20.
UV ?= uv
PY  = cd backend && PYTHONPATH=$(CURDIR)/backend $(UV) run python
.PHONY: setup hooks dev api web serve build test lint typecheck scan golden cuad eval gate demo

setup:            ## install backend (all extras) and frontend dependencies, enable git hooks
	cd backend && $(UV) sync --all-extras
	cd frontend && npm ci
	sh scripts/install-git-hooks.sh

hooks:
	sh scripts/install-git-hooks.sh

api:              ## API + in-process worker on :8077
	$(PY) -m docintel serve

web:              ## frontend dev server on :5177 (proxies /api to :8077)
	cd frontend && npm run dev

dev:              ## API and frontend dev server together
	$(MAKE) -j2 api web

build:            ## production frontend bundle (served by the API at /)
	cd frontend && npm run build

serve: build api  ## build the frontend, then serve everything from :8077

test:
	cd backend && $(UV) run pytest -q

lint:
	cd backend && $(UV) run ruff check docintel tests

typecheck:
	cd frontend && npm run typecheck

scan:             ## secret scan of tracked files and full history
	python3 scripts/secret_scan.py --tracked && python3 scripts/secret_scan.py --history

golden:           ## generate + import the synthetic golden set (60 docs) and the canary subset
	$(PY) -m docintel golden synthetic --n 60

cuad:             ## import 25 real contracts from CUAD (CC BY 4.0)
	$(PY) -m docintel golden cuad --n 25

eval:             ## evaluate PRESET (default cascade) on SPLIT (default test)
	$(PY) -m docintel eval run --preset $(or $(PRESET),cascade) --split $(or $(SPLIT),test)

gate:             ## make gate BASE=<run id> CAND=<run id>  — exit 1 on regression
	$(PY) -m docintel eval gate $(BASE) $(CAND)

demo:             ## generate sample contracts (some with near cancel-by dates) and queue them
	$(PY) -m docintel demo --n 10
