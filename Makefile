SHELL := /bin/bash

PYTHON ?= python3
CONTAINER ?= container

ADDON_VERSION ?= 0.1.0
ADDON_SLUG ?= pesetech_ble_mesh
IMAGE ?=
ADDON_BUILD_DIR ?= .tmp/pesetech-ha-addon-build-$(ADDON_VERSION)

.PHONY: test addon-generate addon-image addon-vars clean

test:
	$(PYTHON) -m unittest discover -s tests

addon-vars:
	@printf 'ADDON_VERSION=%s\n' '$(ADDON_VERSION)'
	@printf 'ADDON_SLUG=%s\n' '$(ADDON_SLUG)'
	@printf 'IMAGE=%s\n' '$(IMAGE)'
	@printf 'ADDON_BUILD_DIR=%s\n' '$(ADDON_BUILD_DIR)'

addon-generate:
	$(PYTHON) scripts/pesetech_make_addon.py \
		--root . \
		--output '$(ADDON_BUILD_DIR)' \
		--slug '$(ADDON_SLUG)' \
		--version '$(ADDON_VERSION)' \
		$(if $(IMAGE),--image '$(IMAGE)',)

addon-image: addon-generate
	$(CONTAINER) build --platform linux/amd64 \
		$(if $(IMAGE),-t '$(IMAGE):$(ADDON_VERSION)',) \
		'$(ADDON_BUILD_DIR)/$(ADDON_SLUG)'

clean:
	rm -rf .tmp .pytest_cache
