package router

import "github.com/golang-jwt/jwt/v5"

type RouterClaims struct {
	jwt.RegisteredClaims
	Stream string `json:"stream"`
	Peer   string `json:"peer"`
}
