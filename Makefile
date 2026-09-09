.PHONY: install check lint types test smoke explorer clean

VENV := .venv/bin

install:
	uv venv --python 3.12
	uv pip install -e ".[dev]"

lint:
	$(VENV)/ruff check .

types:
	$(VENV)/mypy src

test:
	$(VENV)/pytest

check: lint types test

# Prueba de humo completa: genera datos sintéticos y corre el motor de punta a punta.
smoke:
	$(VENV)/chronos data synth --periods 150000
	$(VENV)/chronos backtest --config config/backtest.synthetic.yaml

# El explorador. Es un comando largo: escribe un HTML con todos los pares dentro.
explorer:
	$(VENV)/chronos chart explorer --config config/explorer.yaml

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
