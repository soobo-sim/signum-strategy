# Stage 1: Build wheel
FROM python:3.12-slim AS builder
WORKDIR /build
COPY . .
RUN pip install --no-cache-dir build && python -m build --wheel

# Stage 2: Runtime image with package installed
FROM python:3.12-slim
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
WORKDIR /app

# Copy built wheel so downstream images can COPY --from=... /dist/*.whl
COPY --from=builder /build/dist/ /dist/

LABEL org.opencontainers.image.source="https://github.com/soobo-sim/signum-strategy"
