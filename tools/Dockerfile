# docker build -t rhpd-tools:0.1 tools/
FROM rust:1.88-slim-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY export-parse export-parse
COPY structure/rs structure/rs
ARG JOBS=8
RUN cargo build --release --locked -j $JOBS

FROM python:3.12-slim-bookworm
COPY requirements.txt /tmp/
RUN apt-get update && apt-get install -y --no-install-recommends zstd && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r /tmp/requirements.txt
COPY --from=build /src/target/release/export-parse /src/target/release/universes /src/target/release/dom /src/target/release/relevance /src/target/release/dupes /opt/rhpd/bin/
COPY . /opt/rhpd/tools
ENV RHS_BIN=/opt/rhpd/bin PATH=/opt/rhpd/bin:$PATH
WORKDIR /opt/rhpd/tools
