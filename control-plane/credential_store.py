"""Windows Credential Manager wrapper for Ariadne provider and tunnel secrets."""
from __future__ import annotations

import ctypes
import argparse
import os
import re
import sys
from ctypes import wintypes


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_TARGETS = {
    "GROQ_API_KEY": "Ariadne/Groq/API_KEY",
    "GOOGLE_API_KEY": "Ariadne/GoogleGemini/API_KEY",
    "ARIADNE_MCP_BEARER_TOKEN": "Ariadne/KnowledgeVault/MCP_BEARER_TOKEN",
    "OPENAI_TUNNEL_API_KEY": "Ariadne/KnowledgeVault/OpenAI_TUNNEL_API_KEY",
}


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def _target(reference: str) -> str:
    if reference in _TARGETS:
        return _TARGETS[reference]
    match = re.fullmatch(r"ARIADNE_EXTERNAL_([a-f0-9]{12})_API_KEY", reference)
    if match:
        return f"Ariadne/ExternalProviders/{match.group(1)}/API_KEY"
    raise ValueError("Unknown Ariadne credential reference.")


def get_credential(reference: str) -> str:
    """Resolve an environment override first, then this user's Windows vault."""
    override = os.environ.get(reference, "").strip()
    try:
        target = _target(reference)
    except ValueError:
        return ""
    if override or os.name != "nt":
        return override
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    read = advapi.CredReadW
    read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    read.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    credential = ctypes.POINTER(_CREDENTIAL)()
    if not read(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(credential)):
        return ""
    try:
        item = credential.contents
        return ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize).decode("utf-8")
    finally:
        advapi.CredFree(ctypes.cast(credential, ctypes.c_void_p))


def save_credential(reference: str, secret: str) -> None:
    value = secret.strip()
    if not value:
        raise ValueError("Enter an API key.")
    if os.name != "nt":
        raise OSError("Secure API-key storage is available only on Windows.")
    encoded = value.encode("utf-8")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = _CREDENTIAL(
        Flags=0, Type=_CRED_TYPE_GENERIC, TargetName=_target(reference),
        Comment=f"Ariadne secured runtime credential: {reference}", LastWritten=wintypes.FILETIME(0, 0),
        CredentialBlobSize=len(encoded), CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
        Persist=_CRED_PERSIST_LOCAL_MACHINE, AttributeCount=0, Attributes=None,
        TargetAlias=None, UserName="Ariadne",
    )
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    write = advapi.CredWriteW
    write.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    write.restype = wintypes.BOOL
    if not write(ctypes.byref(credential), 0):
        raise OSError(ctypes.get_last_error(), "Windows Credential Manager could not save the API key.")


def delete_credential(reference: str) -> None:
    if os.name != "nt":
        return
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    delete = advapi.CredDeleteW
    delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    delete.restype = wintypes.BOOL
    if not delete(_target(reference), _CRED_TYPE_GENERIC, 0):
        error = ctypes.get_last_error()
        if error != 1168:
            raise OSError(error, "Windows Credential Manager could not remove the API key.")


def main() -> int:
    """Store an interactive setup secret supplied on stdin, never as an argument."""
    parser = argparse.ArgumentParser(description="Store an Ariadne credential from standard input.")
    parser.add_argument("store-stdin", choices=["store-stdin"])
    parser.add_argument("reference", choices=["OPENAI_TUNNEL_API_KEY", "ARIADNE_MCP_BEARER_TOKEN"])
    args = parser.parse_args()
    secret = sys.stdin.read().strip()
    try:
        save_credential(args.reference, secret)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Stored {args.reference} in Windows Credential Manager.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
