package config

import (
	"testing"
)

func FuzzLoadGrpcConfiguration(f *testing.F) {
	f.Add("5s", true, "30s", "5s", "", "", "")
	f.Add("1s", false, "", "", "", "", "")
	f.Add("", false, "", "", "", "", "")
	f.Add("abc", false, "xyz", "123", "bad", "bad", "bad")
	f.Add("1s", true, "180s", "10s", "5m", "30m", "10s")

	f.Fuzz(func(t *testing.T, minTime string, permitWithoutStream bool,
		timeout, intervalTime, maxConnectionIdle, maxConnectionAge, maxConnectionAgeGrace string) {
		cfg := Grpc{
			Keepalive: Keepalive{
				MinTime:               minTime,
				PermitWithoutStream:   permitWithoutStream,
				Timeout:               timeout,
				IntervalTime:          intervalTime,
				MaxConnectionIdle:     maxConnectionIdle,
				MaxConnectionAge:      maxConnectionAge,
				MaxConnectionAgeGrace: maxConnectionAgeGrace,
			},
		}
		// LoadGrpcConfiguration must not panic.
		_, _ = LoadGrpcConfiguration(cfg)
	})
}

func FuzzParseDuration(f *testing.F) {
	f.Add("1s")
	f.Add("10s")
	f.Add("1m")
	f.Add("1h")
	f.Add("")
	f.Add("invalid")
	f.Add("1.5s")
	f.Add("-1s")
	f.Add("0")
	f.Add("999999h")

	f.Fuzz(func(t *testing.T, input string) {
		// ParseDuration must not panic.
		_, _ = ParseDuration(input)
	})
}
