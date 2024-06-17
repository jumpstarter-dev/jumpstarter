import asyncio
import grpc
import logging
import jgrpc.jumpstarter_pb2_grpc as jrpc
import jgrpc.jumpstarter_pb2 as jpb

from google.protobuf import empty_pb2



class Exporter(jrpc.ForClientServicer):
    async def GetReport(self, request:empty_pb2.Empty,
                        context: grpc.aio.ServicerContext)  -> jpb.ExporterReport:
        pass

async def serve() -> None:
    server = grpc.aio.server()
    jrpc.add_ForClientServicer_to_server(Exporter(), server)
    listen_addr = '[::]:50051'
    server.add_insecure_port(listen_addr)
    logging.info("Starting server on %s", listen_addr)
    await server.start()
    await server.wait_for_termination()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())