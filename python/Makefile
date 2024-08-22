CONTRIB_TARGETS = $(subst contrib/,contrib-,$(wildcard contrib/*))

docs:
	cd docs && make html

docs-watch:
	sphinx-autobuild docs/source docs/build/html

clean:
	rm -rf ./docs/build

test-jumpstarter:
	uv run --isolated --package jumpstarter pytest jumpstarter tests

build-jumpstarter:
	uvx --from build pyproject-build --installer uv --outdir dist

test-contrib-%: contrib/%
	uv run --isolated --package jumpstarter_driver_$(<F) pytest $<

build-contrib-%: contrib/%
	uvx --from build pyproject-build --installer uv --outdir dist $<

test-contrib: $(addprefix test-,$(CONTRIB_TARGETS))

build-contrib: $(addprefix build-,$(CONTRIB_TARGETS))

test: test-jumpstarter test-contrib

build: build-jumpstarter build-contrib

.PHONY: docs test test-jumpstarter test-contrib build build-jumpstarter build-contrib
