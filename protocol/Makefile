setup:
	poetry install

grpc_experiments/grpc/jumpstarter_pb2_grpc.py: proto/jumpstarter.proto
	poetry run python -m grpc_tools.protoc -I ./proto --pyi_out=grpc_experiments/jgrpc \
	                               --grpc_python_out=grpc_experiments/jgrpc \
								   proto/jumpstarter.proto


grpc-python: grpc_experiments/grpc/jumpstarter_pb2_grpc.py

.PHONY: grpc-python install