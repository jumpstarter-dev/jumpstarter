/*
Copyright 2025. The Jumpstarter Authors.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package jumpstarter

// testPEM and testPEM2 are fake PEM certificates shared across multiple test
// files in this package (ca_resolution_test.go and jumpstarter_controller_test.go).
const testPEM = `-----BEGIN CERTIFICATE-----
MIIBpDCCAQmgAwIBAgIUTest1234Test1234Test1234Test1234wCgYIKoZIzj0E
AwIwETEPMA0GA1UEAxMGdGVzdENBMB4XDTI1MDEwMTAwMDAwMFoXDTI2MDEwMTAw
MDAwMFowETEPMA0GA1UEAxMGdGVzdENBMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAE
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAo0IwQDAdBgNVHQ4EFgQUTest12
34Test1234Test1234Test1234wDgYDVR0PAQH/BAQDAgGGMA8GA1UdEwEB/wQFMAMB
Af8wCgYIKoZIzj0EAwIDaAAwZQIwTest1234Test1234Test1234Test1234Test12
34Test1234Test1234Test1234Test1234Test1234Test1234Test1234Test1234==
-----END CERTIFICATE-----
`

const testPEM2 = `-----BEGIN CERTIFICATE-----
MIIBpDCCAQmgAwIBAgIURotated5678Rotated5678Rotated5678Rotated5678w
CgYIKoZIzj0EAwIwFDESMBAGA1UEAxMJcm90YXRlZENBMB4XDTI1MDEwMTAwMDAw
MFoXDTI2MDEwMTAwMDAwMFowFDESMBAGA1UEAxMJcm90YXRlZENBMHYwEAYHKoZI
zj0CAQYFK4EEACIDYgAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAKB
IjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArotated==
-----END CERTIFICATE-----
`
