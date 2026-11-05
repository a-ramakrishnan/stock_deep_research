.PHONY: help install db health ui test lint evidence

help:           ## List the commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

install:        ## Install the dependencies
	uv sync

db:             ## Create the research database (if needed) and migrate it
	uv run sdr db init

health:         ## Check both databases and the API key
	uv run sdr health

ui:             ## The report pages at http://localhost:8502
	uv run streamlit run src/stock_deep_research/ui/app.py --server.port 8502

test:           ## Tests (those needing Postgres skip when it isn't on localhost:5433)
	uv run pytest

lint:           ## Lint and format checks
	uv run ruff check . && uv run ruff format --check .

evidence:       ## Collect a company's shared evidence into a run: make evidence S=LT
	uv run sdr evidence $(S)
