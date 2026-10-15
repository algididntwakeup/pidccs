# Docker Operations

## Build Without Redownloading Everything

Use targeted builds when only one service changed:

```bash
docker compose build api
docker compose build frontend
```

## CPU vs GPU Torch Wheels

The backend image installs CPU-only torch by default (`backend/requirements.txt`
points pip at the PyTorch CPU index). This keeps the image ~3 GB instead of
~10.8 GB and avoids the `[Errno 28] No space left on device` failures on
GitHub runners, where PyPI's Linux torch>=2.13 wheels pull `cuda-toolkit` plus
all `nvidia-*` packages.

On a GPU server, add the CUDA index for the build — the `+cuXXX` wheels then
win over `+cpu` without any other change:

```bash
# once, in the server's .env
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126

# then rebuild the backend image
docker compose build api worker
docker compose up -d
```

The Dockerfile fails the build if `TORCH_INDEX_URL` is set but CPU-only torch
gets installed anyway, so a GPU host cannot silently fall back to CPU inference.

The API and worker now use the same explicit `pidccs-backend:latest` image. Only the API service builds the backend image; the worker reuses it and no longer performs a second large image export. Avoid `docker compose build --no-cache` unless deliberately rebuilding every dependency layer.

The Dockerfiles and `docker-compose.yml` use BuildKit cache mounts (`id=backend-pip-cache`, `id=backend-apt-cache`, `id=npm-cache`, and `id=nextjs-cache`) along with `cache_from` image references on `api`, `worker`, and `frontend`. Keep BuildKit enabled (it is the default in current Docker Desktop releases) so package archives and intermediate builds survive source-code changes and rebuilds.

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
