package service

import (
	"context"
	"net"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/test/bufconn"
)

const testRouterKey = "test-router-key"

type routerPeer struct {
	conn   *grpc.ClientConn
	stream pb.RouterService_StreamClient
}

func routerToken(t *testing.T, subject string) string {
	t.Helper()
	tok := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
		Issuer:    "https://jumpstarter.dev/stream",
		Audience:  jwt.ClaimStrings{"https://jumpstarter.dev/router"},
		Subject:   subject,
		IssuedAt:  jwt.NewNumericDate(time.Now()),
		ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
	})
	signed, err := tok.SignedString([]byte(testRouterKey))
	if err != nil {
		t.Fatal(err)
	}
	return signed
}

// connectPeer gives each peer its own connection, so closing it drops the
// transport the way a killed process does.
func connectPeer(t *testing.T, lis *bufconn.Listener, token string) *routerPeer {
	t.Helper()
	conn, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return lis.DialContext(ctx)
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	ctx := metadata.AppendToOutgoingContext(context.Background(), "authorization", "Bearer "+token)
	stream, err := pb.NewRouterServiceClient(conn).Stream(ctx)
	if err != nil {
		t.Fatal(err)
	}
	return &routerPeer{conn: conn, stream: stream}
}

// pairPeers starts a router and connects two peers to the same stream. The
// first is connected and parked before the second arrives, because the router
// treats them differently: the second one's handler runs the forwarding.
func pairPeers(t *testing.T) (first, second *routerPeer) {
	t.Helper()
	t.Setenv("ROUTER_KEY", testRouterKey)
	lis := bufconn.Listen(bufconnSize)
	s := &RouterService{}
	gs := grpc.NewServer()
	pb.RegisterRouterServiceServer(gs, s)
	go func() { _ = gs.Serve(lis) }()
	t.Cleanup(gs.Stop)

	token := routerToken(t, "stream-1")
	first = connectPeer(t, lis, token)
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, ok := s.pending.Load("stream-1"); ok {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("first peer never became pending")
		}
		time.Sleep(10 * time.Millisecond)
	}
	second = connectPeer(t, lis, token)
	for _, dir := range [][2]*routerPeer{{first, second}, {second, first}} {
		if err := dir[0].stream.Send(&pb.StreamRequest{Payload: []byte("ping")}); err != nil {
			t.Fatal(err)
		}
		msg, err := dir[1].stream.Recv()
		if err != nil || string(msg.GetPayload()) != "ping" {
			t.Fatalf("forwarding not established: %v %v", msg, err)
		}
	}
	return first, second
}

// halfClose ends peer's sending side and gives the router time to read the
// EOF, so the next step happens on a stream the router already sees as
// half-closed.
func halfClose(t *testing.T, peer *routerPeer) {
	t.Helper()
	if err := peer.stream.CloseSend(); err != nil {
		t.Fatal(err)
	}
	time.Sleep(200 * time.Millisecond)
}

// requireStreamEnds fails unless the router ends peer's stream within 3s.
func requireStreamEnds(t *testing.T, peer *routerPeer) {
	t.Helper()
	done := make(chan error, 1)
	go func() {
		_, err := peer.stream.Recv()
		done <- err
	}()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("expected the stream to end, got a message")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("router kept the stream open")
	}
}

// When one peer goes away without a clean close (killed, crashed, network
// lost), the router must end the other peer's stream, even when that peer is
// idle. Otherwise the survivor holds the session open until the lease ends.
func TestRouterEndsSurvivorWhenPeerDisconnects(t *testing.T) {
	for _, tc := range []struct {
		name           string
		killFirst      bool
		halfCloseFirst bool
	}{
		{name: "first peer killed", killFirst: true},
		{name: "second peer killed"},
		// No pipe reads from a half-closed peer any more, so only its stream
		// context shows it is gone.
		{name: "half-closed first peer killed", killFirst: true, halfCloseFirst: true},
		{name: "half-closed second peer killed", halfCloseFirst: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			first, second := pairPeers(t)
			dead, survivor := second, first
			if tc.killFirst {
				dead, survivor = first, second
			}
			if tc.halfCloseFirst {
				halfClose(t, dead)
			}
			_ = dead.conn.Close()
			requireStreamEnds(t, survivor)
		})
	}
}

// A half-close must leave the other direction open, and the stream ends once
// both sides have closed. The client and exporter rely on this TCP-style
// half-close to finish one direction while the other is still sending.
func TestRouterKeepsOtherDirectionAfterHalfClose(t *testing.T) {
	for _, tc := range []struct {
		name        string
		closerFirst bool
	}{
		{name: "first peer half-closes", closerFirst: true},
		{name: "second peer half-closes"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			first, second := pairPeers(t)
			closer, other := second, first
			if tc.closerFirst {
				closer, other = first, second
			}
			halfClose(t, closer)
			if err := other.stream.Send(&pb.StreamRequest{Payload: []byte("late")}); err != nil {
				t.Fatal(err)
			}
			msg, err := closer.stream.Recv()
			if err != nil || string(msg.GetPayload()) != "late" {
				t.Fatalf("data after half-close lost: %v %v", msg, err)
			}
			if err := other.stream.CloseSend(); err != nil {
				t.Fatal(err)
			}
			requireStreamEnds(t, closer)
			requireStreamEnds(t, other)
		})
	}
}
