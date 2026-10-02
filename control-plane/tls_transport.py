"""Verified HTTPS clients and explicit TLS configuration for Ariadne's server."""

import os
import ssl
from collections.abc import Mapping


def client_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    additional_ca = os.environ.get("ARIADNE_TLS_CA_FILE", "").strip()
    if additional_ca:
        context.load_verify_locations(cafile=additional_ca)
    return context


def server_context(environment: Mapping[str, str] | None = None) -> ssl.SSLContext | None:
    environment = os.environ if environment is None else environment
    certificate = environment.get("ARIADNE_TLS_CERT_FILE", "").strip()
    private_key = environment.get("ARIADNE_TLS_KEY_FILE", "").strip()
    required = environment.get("ARIADNE_TLS_REQUIRED", "").casefold() in {"1", "true", "yes"}
    if not certificate and not private_key and not required:
        return None
    if not certificate or not private_key:
        raise RuntimeError("Ariadne HTTPS requires both ARIADNE_TLS_CERT_FILE and ARIADNE_TLS_KEY_FILE.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, private_key)
    return context
