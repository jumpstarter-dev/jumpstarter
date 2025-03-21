from datetime import datetime, timedelta

import grpc
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jumpstarter_protocol import jumpstarter_pb2

SAN = "localhost"


def with_alternative_endpoints(server, endpoints: list[str]):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())

    crt = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([]))
        .issuer_name(x509.Name([]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now())
        .not_valid_after(datetime.now() + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(SAN)]), critical=False)
        .sign(private_key=key, algorithm=hashes.SHA256(), backend=default_backend())
    )
    client_crt = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([]))
        .issuer_name(x509.Name([]))
        .public_key(client_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now())
        .not_valid_after(datetime.now() + timedelta(days=365))
        .sign(private_key=client_key, algorithm=hashes.SHA256(), backend=default_backend())
    )

    pem_crt = crt.public_bytes(serialization.Encoding.PEM)
    pem_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    pem_client_crt = client_crt.public_bytes(serialization.Encoding.PEM)
    pem_client_key = client_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    server_credentials = grpc.ssl_server_credentials(
        [(pem_key, pem_crt)], root_certificates=pem_client_crt, require_client_auth=True
    )

    endpoints_pb = []
    for endpoint in endpoints:
        server.add_secure_port(endpoint, server_credentials)
        endpoints_pb.append(
            jumpstarter_pb2.Endpoint(
                endpoint=endpoint,
                certificate=pem_crt,
                client_certificate=pem_client_crt,
                client_private_key=pem_client_key,
            ),
        )

    return endpoints_pb
