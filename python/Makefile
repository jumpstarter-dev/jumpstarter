CONTRIB_TARGETS = $(subst contrib/,test-contrib-,$(wildcard contrib/*))

docs:
	cd docs && make html

docs-watch:
	sphinx-autobuild docs/source docs/build/html

clean:
	rm -rf ./docs/build

test-jumpstarter:
	uv run --isolated --package jumpstarter pytest jumpstarter tests

test-contrib-%: contrib/%
	uv run --isolated --package jumpstarter_driver_$(<F) pytest $<

test-contrib: $(CONTRIB_TARGETS)

test: test-jumpstarter test-contrib

.PHONY: docs test test-jumpstarter test-contrib
