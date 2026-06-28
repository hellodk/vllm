# otel-tls — air-gapped mTLS material staging (ANS-5)

Stage the PEM files the OTEL agent uses to authenticate to the gateway /
VictoriaMetrics / Fleet OTLP. Nothing is generated on the target node.

Place here:

```
ca.crt                 # cluster CA (shared)
<node_id>.crt          # per-node agent certificate (e.g. hydra-fast-01.crt)
<node_id>.key          # per-node agent private key  (e.g. hydra-fast-01.key)
```

`<node_id>` matches `hydra_node_id` from `cluster.yml`. If you use a single shared
agent cert, override `otel_tls_cert_src` / `otel_tls_key_src` to that filename.

Issue certs from your internal CA / Salt PKI on a connected host, then copy them
into this directory before running `site.yml` / `mac-monitoring.yml`.

> Private keys and certs are git-ignored (see `.gitignore`) — never commit key
> material. Only this README is tracked.
