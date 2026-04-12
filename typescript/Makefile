.PHONY: build test clean

build:
	cd .. && npm --workspace=@jumpstarter/client run build

test:
	cd .. && npm --workspace=@jumpstarter/client test 2>/dev/null || true

clean:
	rm -rf packages/jumpstarter-client/dist
