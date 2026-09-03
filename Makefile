SHELL := /bin/bash

PYTHON ?= python3

.PHONY: test lint build clean

test:
	$(PYTHON) -m unittest discover -s tests

lint:
	ruff check app tests

build:
	docker build -t pesetech2mqtt .

clean:
	git clean -fdX .tmp tests/__pycache__ app/__pycache__
