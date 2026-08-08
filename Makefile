SHELL := /bin/bash

PYTHON ?= python3
CONTAINER ?= container

.PHONY: test addon-image addon-image-amd64 addon-image-aarch64 clean

test:
	PYTHONPATH=pesetech_ble_mesh $(PYTHON) -m unittest discover -s tests

addon-image: addon-image-amd64 addon-image-aarch64

addon-image-amd64:
	$(CONTAINER) build --platform linux/amd64 pesetech_ble_mesh

addon-image-aarch64:
	$(CONTAINER) build --platform linux/arm64 pesetech_ble_mesh

clean:
	git clean -fdX .tmp tests/__pycache__ pesetech_ble_mesh/app/__pycache__
