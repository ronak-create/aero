.PHONY: install run tui test clean build

install:
	pip install -e .

run:
	python cli.py

tui:
	python cli.py --tui

test:
	python run_tests.py -v

clean:
	rm -rf build/ dist/ *.egg-info __pycache__ **/__pycache__ .pytest_cache

build:
	pip install build && python -m build
