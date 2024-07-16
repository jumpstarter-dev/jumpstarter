package main

import (
	"context"
	"log"
	"net"
	"sync"

	"github.com/google/uuid"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"
)

type ControllerService struct {
	pb.UnimplementedControllerServiceServer
	exportersLock sync.RWMutex
	exporters     map[string]Exporter
	listenersLock sync.RWMutex
	listeners     map[string]listenContext
}

type RouterService struct {
	pb.UnimplementedRouterServiceServer
	pendingLock sync.RWMutex
	pending     map[string]streamContext
}

type listenContext struct {
	cancel context.CancelFunc
	stream pb.ControllerService_ListenServer
}

type streamContext struct {
	cancel context.CancelFunc
	stream pb.RouterService_StreamServer
}

type Exporter struct {
	Labels       map[string]string
	DeviceReport []*pb.DeviceReport
}

func (c *ControllerService) Register(
	ctx context.Context,
	req *pb.RegisterRequest,
) (*pb.RegisterResponse, error) {
	c.exportersLock.Lock()
	defer c.exportersLock.Unlock()
	c.exporters[req.GetUuid()] = Exporter{
		Labels:       req.GetLabels(),
		DeviceReport: req.GetDeviceReport(),
	}
	return &pb.RegisterResponse{}, nil
}

func (c *ControllerService) Bye(
	ctx context.Context,
	req *pb.ByeRequest,
) (*emptypb.Empty, error) {
	c.exportersLock.Lock()
	defer c.exportersLock.Unlock()
	delete(c.exporters, req.GetUuid())
	return &emptypb.Empty{}, nil
}

func (c *ControllerService) Listen(req *pb.ListenRequest, stream pb.ControllerService_ListenServer) error {
	ctx := stream.Context()

	exporter, err := service.BearerTokenFromContext(ctx)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	lctx := listenContext{
		cancel: cancel,
		stream: stream,
	}

	c.listenersLock.Lock()
	if _, ok := c.listeners[exporter]; ok {
		return status.Errorf(codes.AlreadyExists, "exporter is already listening")
	}
	c.listeners[exporter] = lctx
	c.listenersLock.Unlock()

	defer func() {
		c.listenersLock.Lock()
		delete(c.listeners, exporter)
		c.listenersLock.Unlock()
	}()

	select {
	case <-ctx.Done():
		return nil
	}
}

func (c *ControllerService) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	c.listenersLock.RLock()
	listener, ok := c.listeners[req.GetUuid()]
	c.listenersLock.RUnlock()

	if !ok {
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	stream, _ := uuid.NewUUID()

	// TODO: find best router from list
	endpoint := "127.0.0.1:8083"

	if err := listener.stream.Send(&pb.ListenResponse{
		RouterEndpoint: endpoint,
		RouterToken:    stream.String(),
		DeviceUuid:     req.DeviceUuid,
	}); err != nil {
		return nil, err
	}

	return &pb.DialResponse{
		RouterEndpoint: endpoint,
		RouterToken:    stream.String(),
	}, nil
}

func (c *ControllerService) ListExporters(
	ctx context.Context,
	req *pb.ListExportersRequest,
) (*pb.ListExportersResponse, error) {
	c.exportersLock.RLock()
	defer c.exportersLock.RUnlock()
	exporters := []*pb.GetReportResponse{}

	for uuid, exporter := range c.exporters {
		mismatch := false
		for key, value := range req.GetLabels() {
			if v, ok := exporter.Labels[key]; !ok || v != value {
				mismatch = true
				break
			}
		}
		if !mismatch {
			exporters = append(exporters, &pb.GetReportResponse{
				Uuid:         uuid,
				Labels:       exporter.Labels,
				DeviceReport: exporter.DeviceReport,
			})
		}
	}

	return &pb.ListExportersResponse{
		Exporters: exporters,
	}, nil

}

func (c *ControllerService) GetExporter(
	ctx context.Context,
	req *pb.GetExporterRequest,
) (*pb.GetExporterResponse, error) {
	c.exportersLock.RLock()
	defer c.exportersLock.RUnlock()
	if exporter, ok := c.exporters[req.GetUuid()]; ok {
		return &pb.GetExporterResponse{
			Exporter: &pb.GetReportResponse{
				Uuid:         req.GetUuid(),
				Labels:       exporter.Labels,
				DeviceReport: exporter.DeviceReport,
			},
		}, nil
	} else {
		return nil, status.Errorf(codes.NotFound, "no such device")
	}
}

func (c *ControllerService) LeaseExporter(
	ctx context.Context,
	req *pb.LeaseExporterRequest,
) (*pb.LeaseExporterResponse, error) {
	return &pb.LeaseExporterResponse{
		LeaseExporterResponseOneof: &pb.LeaseExporterResponse_Success{
			Success: &pb.LeaseExporterResponseSuccess{
				Duration: req.Duration,
			},
		},
	}, nil
}

func (c *ControllerService) ReleaseExporter(
	ctx context.Context,
	req *pb.ReleaseExporterRequest,
) (*pb.ReleaseExporterResponse, error) {
	return &pb.ReleaseExporterResponse{
		ReleaseExporterResponseOneof: &pb.ReleaseExporterResponse_Success{
			Success: &pb.ReleaseExporterResponseSuccess{},
		},
	}, nil
}

func (r *RouterService) Stream(stream pb.RouterService_StreamServer) error {
	ctx := stream.Context()

	streamName, err := service.BearerTokenFromContext(ctx)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	sctx := streamContext{
		cancel: cancel,
		stream: stream,
	}

	r.pendingLock.RLock()
	s, ok := r.pending[streamName]
	r.pendingLock.RUnlock()

	if ok {
		defer s.cancel()
		return service.Forward(ctx, stream, s.stream)
	} else {
		r.pendingLock.Lock()
		r.pending[streamName] = sctx
		r.pendingLock.Unlock()
	}

	select {
	case <-ctx.Done():
		return nil
	}
}

func main() {
	server := grpc.NewServer()

	pb.RegisterControllerServiceServer(server, &ControllerService{
		exporters: make(map[string]Exporter),
		listeners: make(map[string]listenContext),
	})
	pb.RegisterRouterServiceServer(server, &RouterService{
		pending: make(map[string]streamContext),
	})

	listener, err := net.Listen("tcp", ":8083")
	if err != nil {
		log.Fatal(err)
	}

	log.Fatal(server.Serve(listener))
}
