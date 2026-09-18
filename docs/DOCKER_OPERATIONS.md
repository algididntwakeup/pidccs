# Docker Operations

## Build Without Redownloading Everything

Use targeted builds when only one service changed:

```bash
docker compose build api
docker compose build frontend
```

The API and worker now use the same explicit `pidccs-backend:latest` image. Only the API service builds the backend image; the worker reuses it and no longer performs a second large image export. Avoid `docker compose build --no-cache` unless deliberately rebuilding every dependency layer.

The Dockerfiles use BuildKit cache mounts for APT, pip, and npm. Keep BuildKit enabled (it is the default in current Docker Desktop releases) so package archives survive source-code rebuilds.

Start only the required service and its dependencies:

```bash
docker compose up -d postgres redis api
docker compose up -d worker
docker compose up -d frontend
```

`postgres_data`, `pip_cache`, uploaded drawing data, and model weights are named or bind-mounted storage. Do not run `docker compose down -v` unless database and cache volumes are intentionally being deleted.

## Export Performance

Export generation is CPU-bound, so the FastAPI route delegates PyMuPDF/OpenCV work to a worker thread instead of blocking the async event loop. PNG export caches the rendered base drawing by source file, modification time, DPI, and rotation. Repeated exports no longer re-render a PDF at full resolution unless the source or render parameters change.

Export filenames are deterministic per drawing/format/mode, so repeated downloads replace the current deliverable instead of creating an unbounded UUID-named file in storage.
