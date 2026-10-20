# Public offline outbound-proxy TLS fixtures

**The committed private key is deliberately PUBLIC TEST DATA, not a secret.**
Never use this key/certificate for a real service, real account, or production
TLS. Do not install this certificate in a machine/user trust store. The suite
loads it only into an explicitly scoped test `SSLContext`.

- `probe.invalid-cert.pem`: self-signed RSA-2048/SHA-256 test certificate;
  SAN is exactly `DNS:probe.invalid` (a reserved `.invalid` name).
- `probe.invalid-key.pem`: matching **public test-only private key**.
- `openssl.cnf`: public subject/extensions used for the one-time generation.

The certificate is valid from 2026-10-20 UTC until 2126-09-26 UTC. A generously
long lifetime avoids routine fixture expiration; normal TLS chain and hostname
checks remain enabled in the tests. The fake proxy listens only on `127.0.0.1`
and becomes the TLS origin itself, so it never resolves or forwards the target.
The suite additionally blocks all non-loopback DNS lookups and socket connects,
including an accidental direct connection caused by a production regression.

## Run (Python standard library only; no OpenSSL required)

From the repository root:

```console
python -B tests/_test_outbound_proxy_tls.py
python -B tests/run_all.py outbound_proxy_tls
```

The suite directly exercises real `qoder_netproxy.build_opener(...).open(...)`,
HTTP Basic CONNECT authentication, 407, default self-signed-certificate rejection,
SAN/hostname validation, explicit-proxy behavior under `NO_PROXY=*` and a forced
`urllib.request.proxy_bypass=True`, and authenticated SOCKS5 followed by TLS.
Settings tests operate only in temporary directories, checking corrupt JSON and
invalid top-level types fail closed and proxy updates preserve unrelated keys.
Resolver failures must not return a cached opener or fall back to environment
proxies/direct connections. No real credentials or external traffic are used.

## One-time regeneration (maintainer action only)

The PEM files were generated locally with the available Git-for-Windows OpenSSL.
The test suite never invokes OpenSSL and never generates certificates at runtime.
The equivalent command, from the repository root, is:

```powershell
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 36500 -config tests/fixtures/outbound-proxy/openssl.cnf -keyout tests/fixtures/outbound-proxy/probe.invalid-key.pem -out tests/fixtures/outbound-proxy/probe.invalid-cert.pem
```

After intentional regeneration, retain the leading `# PUBLIC ... TEST ...`
comments in both PEM files and update the documented validity dates above.
Changing/replacing the fixtures should not weaken the client SSL context or
remove either negative certificate check.
