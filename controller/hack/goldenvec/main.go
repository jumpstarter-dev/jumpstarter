// goldenvec emits golden vectors for the Rust controller rewrite
// (rust/jumpstarter-controller-auth). The vectors lock the bit-exact ES256 key
// derivation performed by internal/oidc.NewSignerFromSeed
// (sha256(CONTROLLER_KEY) -> math/rand seeded source -> keygen.ECDSALegacy(P256))
// as well as Go-signed internal ES256 and router HS256 tokens.
//
// Generation (writes derivation.json, tokens.json, gorand.json to -out):
//
//	cd controller && go run ./hack/goldenvec -out ../rust/jumpstarter-controller-auth/tests/golden
//
// Verification mode (used by CI to check Rust-minted internal tokens with the
// Go validator; exits 0 on success, 1 on failure):
//
//	go run ./hack/goldenvec -verify-token <jwt> -seed <base64 CONTROLLER_KEY>
package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"math/rand"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"time"

	"filippo.io/keygen"
	"github.com/golang-jwt/jwt/v5"
	"github.com/zitadel/oidc/v3/pkg/op"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
)

// Defaults from cmd/main.go where the internal signer is constructed:
//
//	oidc.NewSignerFromSeed([]byte(os.Getenv("CONTROLLER_KEY")), "https://localhost:8085", "jumpstarter")
const (
	defaultIssuer   = "https://localhost:8085"
	defaultAudience = "jumpstarter"

	goldenSubject   = "client:default:golden:uid-1234"
	goldenRouterKey = "golden-router-key"

	// Router token claim constants, mirroring internal/service/controller_service.go:866-874.
	routerIssuer   = "https://jumpstarter.dev/stream"
	routerAudience = "https://jumpstarter.dev/router"

	hundredYears = 100 * 365 * 24 * time.Hour
)

type derivationVector struct {
	Name             string `json:"name"`
	ControllerKeyB64 string `json:"controller_key_b64"`
	// Debug aids: sha256(CONTROLLER_KEY) and the int64 math/rand seed
	// (int64(binary.BigEndian.Uint64(hash[:8]))), as a decimal string since
	// it exceeds JSON's exact-integer range.
	Sha256Hex string `json:"sha256_hex"`
	SeedInt64 string `json:"seed_int64"`
	DHex      string `json:"d_hex"`
	XHex      string `json:"x_hex"`
	YHex      string `json:"y_hex"`
}

type derivationFile struct {
	Comment string             `json:"_comment"`
	Vectors []derivationVector `json:"vectors"`
}

type internalToken struct {
	SeedB64  string          `json:"seed_b64"`
	Issuer   string          `json:"issuer"`
	Audience string          `json:"audience"`
	Subject  string          `json:"subject"`
	Token    string          `json:"token"`
	Claims   json.RawMessage `json:"claims"`
	JWKS     json.RawMessage `json:"jwks"`
}

type routerToken struct {
	Token  string          `json:"token"`
	Claims json.RawMessage `json:"claims"`
}

type routerTokens struct {
	Key    string        `json:"key"`
	Tokens []routerToken `json:"tokens"`
}

type tokensFile struct {
	Comment  string        `json:"_comment"`
	Internal internalToken `json:"internal"`
	Router   routerTokens  `json:"router"`
}

type gorandCase struct {
	// Decimal string: int64 seeds exceed JSON's exact-integer range.
	Seed string `json:"seed"`
	// First 20 Int63() values from a fresh rand.New(rand.NewSource(seed)).
	Int63 []string `json:"int63"`
	// First 64 bytes from Read() on a fresh source, hex-encoded.
	ReadHex string `json:"read_hex"`
	// Same 64 bytes produced by three consecutive Read calls of 7, 3 and 54
	// bytes on a fresh source (locks readVal/readPos persistence).
	ReadSplitHex string `json:"read_split_hex"`
}

type gorandFile struct {
	Comment string       `json:"_comment"`
	Cases   []gorandCase `json:"cases"`
}

func mustDecodeHex(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		panic(err)
	}
	return b
}

// deriveKey replicates internal/oidc.NewSignerFromSeed's key derivation
// verbatim. main() cross-checks the resulting public key against the one the
// real NewSignerFromSeed exposes, so this copy cannot drift silently.
func deriveKey(seed []byte) (*ecdsa.PrivateKey, [32]byte, int64) {
	hash := sha256.Sum256(seed)
	intSeed := int64(binary.BigEndian.Uint64(hash[:8]))
	source := rand.NewSource(intSeed)
	reader := rand.New(source)
	key, err := keygen.ECDSALegacy(elliptic.P256(), reader)
	if err != nil {
		panic(err)
	}
	return key, hash, intSeed
}

func pad32Hex(b []byte) string {
	out := make([]byte, 32)
	copy(out[32-len(b):], b)
	return hex.EncodeToString(out)
}

func buildDerivation() derivationFile {
	seeds := []struct {
		name string
		seed []byte
	}{
		{"empty", []byte("")},
		{"single-char", []byte("a")},
		{"short-ascii", []byte("test")},
		{"typical-secret", []byte("supersecret-controller-key")},
		{"golden-fixed", []byte("golden-controller-key")},
		{"long-ascii", []byte("The quick brown fox jumps over the lazy dog. " +
			"Pack my box with five dozen liquor jugs. " +
			"Sphinx of black quartz, judge my vow.")},
		{"utf8-multibyte", []byte("秘密の鍵🔑ключ-контролера")},
		{"binary-32", mustDecodeHex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")},
		{"binary-64", mustDecodeHex("ffeeddccbbaa99887766554433221100f0e0d0c0b0a090807060504030201000" +
			"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")},
		{"binary-high-bytes", mustDecodeHex("ffffffffffffffffffffffffffffffff")},
	}

	out := derivationFile{
		Comment: "Generated by controller/hack/goldenvec. ES256 key derivation vectors for " +
			"internal/oidc.NewSignerFromSeed: sha256(controller_key) -> math/rand(int64 BE of hash[:8]) " +
			"-> keygen.ECDSALegacy(P256). d/x/y are 32-byte left-padded big-endian hex.",
	}
	for _, s := range seeds {
		key, hash, intSeed := deriveKey(s.seed)

		// Cross-check against the real production constructor so the local
		// deriveKey copy can never drift from internal/oidc.
		signer, err := oidc.NewSignerFromSeed(s.seed, defaultIssuer, defaultAudience)
		if err != nil {
			panic(err)
		}
		pub, ok := signer.Key().(*ecdsa.PublicKey)
		if !ok || pub.X.Cmp(key.PublicKey.X) != 0 || pub.Y.Cmp(key.PublicKey.Y) != 0 {
			panic(fmt.Sprintf("seed %q: deriveKey diverged from oidc.NewSignerFromSeed", s.name))
		}

		out.Vectors = append(out.Vectors, derivationVector{
			Name:             s.name,
			ControllerKeyB64: base64.StdEncoding.EncodeToString(s.seed),
			Sha256Hex:        hex.EncodeToString(hash[:]),
			SeedInt64:        fmt.Sprintf("%d", intSeed),
			DHex:             pad32Hex(key.D.Bytes()),
			XHex:             pad32Hex(key.PublicKey.X.Bytes()),
			YHex:             pad32Hex(key.PublicKey.Y.Bytes()),
		})
	}
	return out
}

func rawClaims(token string) json.RawMessage {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		panic("malformed JWT")
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		panic(err)
	}
	return json.RawMessage(payload)
}

func buildTokens() tokensFile {
	seed := []byte("golden-controller-key")

	signer, err := oidc.NewSignerFromSeed(seed, defaultIssuer, defaultAudience)
	if err != nil {
		panic(err)
	}
	signer.SetTokenLifetime(hundredYears)
	internal, err := signer.Token(goldenSubject)
	if err != nil {
		panic(err)
	}
	if err := signer.Validate(internal); err != nil {
		panic(err)
	}

	// Capture the JWKS document exactly as the Go signer serves it on
	// GET /jwks (internal/oidc/op.go Register -> op.Keys).
	rec := httptest.NewRecorder()
	op.Keys(rec, httptest.NewRequest("GET", "/jwks", nil), signer)
	if rec.Code != 200 {
		panic(fmt.Sprintf("op.Keys returned %d", rec.Code))
	}

	// Router HS256 tokens, claim-for-claim as minted in
	// internal/service/controller_service.go:866-874 but with fixed
	// sub/jti and a 100-year expiry so the fixtures stay verifiable.
	now := time.Now()
	mintRouter := func(sub, jti string) routerToken {
		tok, err := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
			Issuer:    routerIssuer,
			Subject:   sub,
			Audience:  []string{routerAudience},
			ExpiresAt: jwt.NewNumericDate(now.Add(hundredYears)),
			NotBefore: jwt.NewNumericDate(now),
			IssuedAt:  jwt.NewNumericDate(now),
			ID:        jti,
		}).SignedString([]byte(goldenRouterKey))
		if err != nil {
			panic(err)
		}
		return routerToken{Token: tok, Claims: rawClaims(tok)}
	}

	return tokensFile{
		Comment: "Generated by controller/hack/goldenvec. internal = ES256 token signed by the " +
			"seed-derived key (exp +100y); jwks = exact GET /jwks body served by the Go signer; " +
			"router = HS256 tokens signed with `key`, claims as minted by Dial/Listen.",
		Internal: internalToken{
			SeedB64:  base64.StdEncoding.EncodeToString(seed),
			Issuer:   defaultIssuer,
			Audience: defaultAudience,
			Subject:  goldenSubject,
			Token:    internal,
			Claims:   rawClaims(internal),
			JWKS:     json.RawMessage(rec.Body.Bytes()),
		},
		Router: routerTokens{
			Key: goldenRouterKey,
			Tokens: []routerToken{
				mintRouter("f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "0c7f9a52-1a3b-4d5e-8f90-abcdef012345"),
				mintRouter("9e107d9d-372b-4b6f-b1ac-5f18d3f3a001", "5a2e6b3c-77aa-4e21-9c4d-1234567890ab"),
			},
		},
	}
}

func buildGorand() gorandFile {
	// int64(binary.BigEndian.Uint64(sha256("")[:8])) — the seed the empty
	// CONTROLLER_KEY produces; keeps one case aligned with derivation.json.
	emptyHash := sha256.Sum256(nil)
	emptySeed := int64(binary.BigEndian.Uint64(emptyHash[:8]))

	seeds := []int64{
		1,
		0,          // normalized to 89482311
		-1,         // negative wrap: -1 % int32max + int32max
		89482311,   // the zero-replacement constant itself
		2147483646, // int32max - 1
		2147483647, // int32max: seed %= int32max -> 0 -> 89482311
		2147483648, // int32max + 1 -> 1
		-9223372036854775808, // int64 min
		9223372036854775807,  // int64 max
		emptySeed,
	}

	out := gorandFile{
		Comment: "Generated by controller/hack/goldenvec. Raw Go math/rand fixtures: fresh " +
			"rand.New(rand.NewSource(seed)) per field; int63 = first 20 Int63() values (decimal " +
			"strings); read_hex = first 64 Read() bytes; read_split_hex = same source read in " +
			"7+3+54 byte chunks (locks readVal/readPos persistence).",
	}
	for _, seed := range seeds {
		c := gorandCase{Seed: fmt.Sprintf("%d", seed)}

		r := rand.New(rand.NewSource(seed))
		for i := 0; i < 20; i++ {
			c.Int63 = append(c.Int63, fmt.Sprintf("%d", r.Int63()))
		}

		r = rand.New(rand.NewSource(seed))
		buf := make([]byte, 64)
		if _, err := r.Read(buf); err != nil {
			panic(err)
		}
		c.ReadHex = hex.EncodeToString(buf)

		r = rand.New(rand.NewSource(seed))
		split := make([]byte, 64)
		for _, chunk := range [][]byte{split[:7], split[7:10], split[10:]} {
			if _, err := r.Read(chunk); err != nil {
				panic(err)
			}
		}
		c.ReadSplitHex = hex.EncodeToString(split)

		out.Cases = append(out.Cases, c)
	}
	return out
}

func writeJSON(dir, name string, v any) {
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		panic(err)
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, append(data, '\n'), 0o644); err != nil {
		panic(err)
	}
	fmt.Println("wrote", path)
}

func main() {
	out := flag.String("out", ".", "output directory for generated golden files")
	verifyToken := flag.String("verify-token", "", "verify an internal ES256 token instead of generating vectors")
	seedB64 := flag.String("seed", "", "base64 CONTROLLER_KEY seed (required with -verify-token)")
	issuer := flag.String("issuer", defaultIssuer, "issuer for -verify-token")
	audience := flag.String("audience", defaultAudience, "audience for -verify-token")
	flag.Parse()

	if *verifyToken != "" {
		seed, err := base64.StdEncoding.DecodeString(*seedB64)
		if err != nil {
			fmt.Fprintln(os.Stderr, "invalid -seed base64:", err)
			os.Exit(1)
		}
		signer, err := oidc.NewSignerFromSeed(seed, *issuer, *audience)
		if err != nil {
			fmt.Fprintln(os.Stderr, "signer:", err)
			os.Exit(1)
		}
		if err := signer.Validate(*verifyToken); err != nil {
			fmt.Fprintln(os.Stderr, "token INVALID:", err)
			os.Exit(1)
		}
		fmt.Println("token OK")
		return
	}

	writeJSON(*out, "derivation.json", buildDerivation())
	writeJSON(*out, "tokens.json", buildTokens())
	writeJSON(*out, "gorand.json", buildGorand())
}
