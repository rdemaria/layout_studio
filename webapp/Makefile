SHELL := /bin/sh
NPM ?= npm
NODE ?= node
INSTALL_STAMP := node_modules/.layout-studio-install

.DEFAULT_GOAL := standalone
.NOTPARALLEL:

.PHONY: all build install standalone hosted lint test check clean

all: standalone

build: standalone

$(INSTALL_STAMP): package.json package-lock.json
	$(NPM) ci
	touch $(INSTALL_STAMP)

install: $(INSTALL_STAMP)

standalone: install
	$(NPM) run build:standalone
	test -s build/index.html

hosted: install
	$(NPM) run build

lint: install
	$(NPM) run lint

test: install
	$(NODE) --test tests/layout-data.test.mjs
	$(NODE) --test --test-name-pattern='viewer|named-frame|dependency hierarchy|collapsible controls|top-bar action' tests/ui-components.test.mjs

check: lint hosted test standalone

clean:
	rm -rf -- dist .next standalone/dist
	rm -f -- $(INSTALL_STAMP)
