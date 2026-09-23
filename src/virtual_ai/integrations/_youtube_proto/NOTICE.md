# YouTube streamList protocol snapshot

Source: https://developers.google.com/youtube/v3/live/streaming-live-chat?hl=en

Retrieved: 2026-09-23. The `stream_list.proto` code sample is licensed by Google
under Apache License 2.0; see the accompanying `LICENSE-2.0.txt`.
No upstream Git revision is published for this documentation snapshot.

Original UTF-8/LF sample SHA-256:
`1e0588932437ecd6b4c99b3da9a2fa1c9351db3665be22cb28dfe31a62672e98`

Local modification: added `import "google/protobuf/duration.proto";` with an
explanatory comment. The published sample references `google.protobuf.Duration`
without importing its definition, so the unmodified snapshot fails compilation.
No service names, field numbers, enum values, or other declarations were changed.

Patched UTF-8/LF proto SHA-256:
`fa3bb3b0cb9be861e2be340f8fb163032e9331f419b60547f4f5749106bcce88`

Generation tools: grpcio-tools 1.84.0, protobuf 7.36.2. Runtime: grpcio 1.84.0,
protobuf 7.36.2. Exact versions are pinned in pyproject.toml and uv.lock.
Generated Python files are checked in and excluded from Ruff rewriting.

Regenerate from the repository root, without downloading a newer schema:

```powershell
python -m uv sync --locked
python -m uv run --locked python -m grpc_tools.protoc -Isrc --python_out=src --grpc_python_out=src src/virtual_ai/integrations/_youtube_proto/stream_list.proto
```

Regeneration was compared byte-for-byte in a temporary output directory.
The gRPC local test verifies serialization and the service method path;
it does not prove the current Google service accepts this snapshot.
