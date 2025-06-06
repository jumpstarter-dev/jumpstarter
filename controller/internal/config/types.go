package config

import (
	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
)

type Config struct {
	Authentication Authentication `json:"authentication"`
	Provisioning   Provisioning   `json:"provisioning"`
	Grpc           Grpc           `json:"grpc"`
}

type Authentication struct {
	Internal Internal                            `json:"internal"`
	JWT      []apiserverv1beta1.JWTAuthenticator `json:"jwt"`
}

type Provisioning struct {
	Enabled bool `json:"enabled"`
}

type Internal struct {
	Prefix string `json:"prefix"`
}

type Grpc struct {
	Keepalive Keepalive `json:"keepalive"`
}

type Keepalive struct {
	MinTime             string `json:"minTime"`
	PermitWithoutStream bool   `json:"permitWithoutStream"`
}

type Router map[string]RouterEntry

type RouterEntry struct {
	Endpoint string            `json:"endpoint"`
	Labels   map[string]string `json:"labels"`
}
