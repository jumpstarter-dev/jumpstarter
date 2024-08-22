docs:
	cd docs && make html

docs-watch:
	sphinx-autobuild docs/source docs/build/html

clean:
	rm -rf ./docs/build

test-contrib-%: contrib/%
	uv run --isolated --package jumpstarter_driver_$(<F) pytest $<

test-contrib: $(subst contrib/,test-contrib-,$(wildcard contrib/*))

.PHONY: docs
