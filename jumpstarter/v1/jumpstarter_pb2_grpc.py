# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
"""Client and server classes corresponding to protobuf-defined services."""
import grpc

from google.protobuf import empty_pb2 as google_dot_protobuf_dot_empty__pb2
from jumpstarter.v1 import jumpstarter_pb2 as jumpstarter_dot_v1_dot_jumpstarter__pb2


class ControllerServiceStub(object):
    """A service where a exporter can connect to make itself available
    """

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.Register = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/Register',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.RegisterRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.RegisterResponse.FromString,
                _registered_method=True)
        self.Unregister = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/Unregister',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.UnregisterRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.UnregisterResponse.FromString,
                _registered_method=True)
        self.Listen = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/Listen',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListenRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListenResponse.FromString,
                _registered_method=True)
        self.Dial = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/Dial',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.DialRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.DialResponse.FromString,
                _registered_method=True)
        self.AuditStream = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/AuditStream',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.AuditStreamRequest.SerializeToString,
                response_deserializer=google_dot_protobuf_dot_empty__pb2.Empty.FromString,
                _registered_method=True)
        self.ListExporters = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/ListExporters',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListExportersRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListExportersResponse.FromString,
                _registered_method=True)
        self.GetExporter = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/GetExporter',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetExporterRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetExporterResponse.FromString,
                _registered_method=True)
        self.GetLease = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/GetLease',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetLeaseRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetLeaseResponse.FromString,
                _registered_method=True)
        self.RequestLease = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/RequestLease',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.RequestLeaseRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.RequestLeaseResponse.FromString,
                _registered_method=True)
        self.ReleaseLease = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/ReleaseLease',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ReleaseLeaseRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ReleaseLeaseResponse.FromString,
                _registered_method=True)
        self.ListLeases = channel.unary_unary(
                '/jumpstarter.v1.ControllerService/ListLeases',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListLeasesRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListLeasesResponse.FromString,
                _registered_method=True)


class ControllerServiceServicer(object):
    """A service where a exporter can connect to make itself available
    """

    def Register(self, request, context):
        """Exporter registration
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def Unregister(self, request, context):
        """Exporter disconnection
        Disconnecting with bye will invalidate any existing router tokens
        we will eventually have a mechanism to tell the router this token
        has been invalidated
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def Listen(self, request, context):
        """Exporter listening
        Returns stream tokens for accepting incoming client connections
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def Dial(self, request, context):
        """Client connecting
        Returns stream token for connecting to the desired exporter
        Leases are checked before token issuance
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def AuditStream(self, request, context):
        """Audit events from the exporters
        audit events are used to track the exporter's activity
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def ListExporters(self, request, context):
        """List exporters
        Returns all exporters matching filter
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def GetExporter(self, request, context):
        """Get exporter
        Get information of the exporter of the given uuid
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def GetLease(self, request, context):
        """Get Lease
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def RequestLease(self, request, context):
        """Request Lease
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def ReleaseLease(self, request, context):
        """Release Lease
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def ListLeases(self, request, context):
        """List Leases
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_ControllerServiceServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'Register': grpc.unary_unary_rpc_method_handler(
                    servicer.Register,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.RegisterRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.RegisterResponse.SerializeToString,
            ),
            'Unregister': grpc.unary_unary_rpc_method_handler(
                    servicer.Unregister,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.UnregisterRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.UnregisterResponse.SerializeToString,
            ),
            'Listen': grpc.unary_unary_rpc_method_handler(
                    servicer.Listen,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListenRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListenResponse.SerializeToString,
            ),
            'Dial': grpc.unary_unary_rpc_method_handler(
                    servicer.Dial,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.DialRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.DialResponse.SerializeToString,
            ),
            'AuditStream': grpc.unary_unary_rpc_method_handler(
                    servicer.AuditStream,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.AuditStreamRequest.FromString,
                    response_serializer=google_dot_protobuf_dot_empty__pb2.Empty.SerializeToString,
            ),
            'ListExporters': grpc.unary_unary_rpc_method_handler(
                    servicer.ListExporters,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListExportersRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListExportersResponse.SerializeToString,
            ),
            'GetExporter': grpc.unary_unary_rpc_method_handler(
                    servicer.GetExporter,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetExporterRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetExporterResponse.SerializeToString,
            ),
            'GetLease': grpc.unary_unary_rpc_method_handler(
                    servicer.GetLease,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetLeaseRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetLeaseResponse.SerializeToString,
            ),
            'RequestLease': grpc.unary_unary_rpc_method_handler(
                    servicer.RequestLease,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.RequestLeaseRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.RequestLeaseResponse.SerializeToString,
            ),
            'ReleaseLease': grpc.unary_unary_rpc_method_handler(
                    servicer.ReleaseLease,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ReleaseLeaseRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ReleaseLeaseResponse.SerializeToString,
            ),
            'ListLeases': grpc.unary_unary_rpc_method_handler(
                    servicer.ListLeases,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListLeasesRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.ListLeasesResponse.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'jumpstarter.v1.ControllerService', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))
    server.add_registered_method_handlers('jumpstarter.v1.ControllerService', rpc_method_handlers)


 # This class is part of an EXPERIMENTAL API.
class ControllerService(object):
    """A service where a exporter can connect to make itself available
    """

    @staticmethod
    def Register(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/Register',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.RegisterRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.RegisterResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def Unregister(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/Unregister',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.UnregisterRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.UnregisterResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def Listen(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/Listen',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.ListenRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.ListenResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def Dial(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/Dial',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.DialRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.DialResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def AuditStream(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/AuditStream',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.AuditStreamRequest.SerializeToString,
            google_dot_protobuf_dot_empty__pb2.Empty.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def ListExporters(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/ListExporters',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.ListExportersRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.ListExportersResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def GetExporter(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/GetExporter',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.GetExporterRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.GetExporterResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def GetLease(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/GetLease',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.GetLeaseRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.GetLeaseResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def RequestLease(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/RequestLease',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.RequestLeaseRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.RequestLeaseResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def ReleaseLease(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/ReleaseLease',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.ReleaseLeaseRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.ReleaseLeaseResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def ListLeases(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ControllerService/ListLeases',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.ListLeasesRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.ListLeasesResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)


class ExporterServiceStub(object):
    """A service a exporter can share locally to be used without a server
    Channel/Call credentials are used to authenticate the client, and routing to the right exporter
    """

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.GetReport = channel.unary_unary(
                '/jumpstarter.v1.ExporterService/GetReport',
                request_serializer=google_dot_protobuf_dot_empty__pb2.Empty.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetReportResponse.FromString,
                _registered_method=True)
        self.DriverCall = channel.unary_unary(
                '/jumpstarter.v1.ExporterService/DriverCall',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.DriverCallRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.DriverCallResponse.FromString,
                _registered_method=True)
        self.StreamingDriverCall = channel.unary_stream(
                '/jumpstarter.v1.ExporterService/StreamingDriverCall',
                request_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.StreamingDriverCallRequest.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.StreamingDriverCallResponse.FromString,
                _registered_method=True)
        self.LogStream = channel.unary_stream(
                '/jumpstarter.v1.ExporterService/LogStream',
                request_serializer=google_dot_protobuf_dot_empty__pb2.Empty.SerializeToString,
                response_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.LogStreamResponse.FromString,
                _registered_method=True)


class ExporterServiceServicer(object):
    """A service a exporter can share locally to be used without a server
    Channel/Call credentials are used to authenticate the client, and routing to the right exporter
    """

    def GetReport(self, request, context):
        """Exporter registration
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def DriverCall(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def StreamingDriverCall(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def LogStream(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_ExporterServiceServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'GetReport': grpc.unary_unary_rpc_method_handler(
                    servicer.GetReport,
                    request_deserializer=google_dot_protobuf_dot_empty__pb2.Empty.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.GetReportResponse.SerializeToString,
            ),
            'DriverCall': grpc.unary_unary_rpc_method_handler(
                    servicer.DriverCall,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.DriverCallRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.DriverCallResponse.SerializeToString,
            ),
            'StreamingDriverCall': grpc.unary_stream_rpc_method_handler(
                    servicer.StreamingDriverCall,
                    request_deserializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.StreamingDriverCallRequest.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.StreamingDriverCallResponse.SerializeToString,
            ),
            'LogStream': grpc.unary_stream_rpc_method_handler(
                    servicer.LogStream,
                    request_deserializer=google_dot_protobuf_dot_empty__pb2.Empty.FromString,
                    response_serializer=jumpstarter_dot_v1_dot_jumpstarter__pb2.LogStreamResponse.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'jumpstarter.v1.ExporterService', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))
    server.add_registered_method_handlers('jumpstarter.v1.ExporterService', rpc_method_handlers)


 # This class is part of an EXPERIMENTAL API.
class ExporterService(object):
    """A service a exporter can share locally to be used without a server
    Channel/Call credentials are used to authenticate the client, and routing to the right exporter
    """

    @staticmethod
    def GetReport(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ExporterService/GetReport',
            google_dot_protobuf_dot_empty__pb2.Empty.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.GetReportResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def DriverCall(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/jumpstarter.v1.ExporterService/DriverCall',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.DriverCallRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.DriverCallResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def StreamingDriverCall(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_stream(
            request,
            target,
            '/jumpstarter.v1.ExporterService/StreamingDriverCall',
            jumpstarter_dot_v1_dot_jumpstarter__pb2.StreamingDriverCallRequest.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.StreamingDriverCallResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def LogStream(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_stream(
            request,
            target,
            '/jumpstarter.v1.ExporterService/LogStream',
            google_dot_protobuf_dot_empty__pb2.Empty.SerializeToString,
            jumpstarter_dot_v1_dot_jumpstarter__pb2.LogStreamResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)
