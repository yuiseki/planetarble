# Planetarble runtime image: GDAL + go-pmtiles + mb-util + aria2 + the CLI.
# Build:  docker build -t planetarble .
# Run:    docker run --rm -v "$PWD/workspace:/workspace" planetarble --help
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.11.3

ARG TARGETARCH
ARG PMTILES_VERSION=1.30.3

RUN apt-get update \
    && apt-get install -y --no-install-recommends aria2 ca-certificates curl python3-venv \
    && rm -rf /var/lib/apt/lists/*

# go-pmtiles (single static binary)
RUN case "${TARGETARCH}" in \
      amd64) PMTILES_ARCH=x86_64 ;; \
      arm64) PMTILES_ARCH=arm64 ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" && exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/pmtiles.tgz "https://github.com/protomaps/go-pmtiles/releases/download/v${PMTILES_VERSION}/go-pmtiles_${PMTILES_VERSION}_Linux_${PMTILES_ARCH}.tar.gz" \
    && tar -xzf /tmp/pmtiles.tgz -C /usr/local/bin pmtiles \
    && chmod +x /usr/local/bin/pmtiles \
    && rm /tmp/pmtiles.tgz

# planetarble CLI + mb-util in a venv that can still see the system GDAL bindings
COPY . /opt/planetarble
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir /opt/planetarble mbutil
ENV PATH="/opt/venv/bin:${PATH}"

# data/tmp/output resolve relative to the working directory
WORKDIR /workspace

ENTRYPOINT ["planetarble"]
CMD ["--help"]
