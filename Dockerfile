# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build dependencies
    build-essential \
    pkg-config \
    # MakeMKV dependencies
    libavcodec-dev \
    libssl-dev \
    libexpat1-dev \
    zlib1g-dev \
    # HandBrake
    handbrake-cli \
    # Media tools
    mediainfo \
    libmediainfo-dev \
    # Disc tools
    eject \
    util-linux \
    # udev for disc detection
    udev \
    libudev-dev \
    && rm -rf /var/lib/apt/lists/*

# ============================================
# MakeMKV build stage
# ============================================
FROM base AS makemkv-builder

ARG MAKEMKV_VERSION=1.18.3

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    ca-certificates \
    less \
    qtbase5-dev \
    libqt5opengl5-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

# Download and build MakeMKV
RUN wget -q "https://www.makemkv.com/download/makemkv-bin-${MAKEMKV_VERSION}.tar.gz" \
    && wget -q "https://www.makemkv.com/download/makemkv-oss-${MAKEMKV_VERSION}.tar.gz" \
    && tar xzf makemkv-oss-${MAKEMKV_VERSION}.tar.gz \
    && tar xzf makemkv-bin-${MAKEMKV_VERSION}.tar.gz

RUN cd makemkv-oss-${MAKEMKV_VERSION} \
    && ./configure --prefix=/usr/local \
    && make -j$(nproc) \
    && make install

RUN cd makemkv-bin-${MAKEMKV_VERSION} \
    && echo "yes" | make install

# ============================================
# Final image
# ============================================
FROM base AS runtime

# Copy MakeMKV from builder
COPY --from=makemkv-builder /usr/local/bin/makemkv* /usr/local/bin/
COPY --from=makemkv-builder /usr/local/lib/libmakemkv.so* /usr/local/lib/
COPY --from=makemkv-builder /usr/local/lib/libdriveio.so* /usr/local/lib/
COPY --from=makemkv-builder /usr/local/lib/libmmbd.so* /usr/local/lib/

# Update library cache
RUN ldconfig

# Create non-root user with video group access
RUN groupadd -g 1000 riparr \
    && useradd -u 1000 -g riparr -G cdrom,video -m riparr

# Create directories
RUN mkdir -p /data/raw /data/media /config \
    && chown -R riparr:riparr /data /config

WORKDIR /app

# Copy application code
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install the application
RUN pip install --no-cache-dir .

# Switch to non-root user
USER riparr

# Environment defaults
ENV RIPARR_RAW_DIR=/data/raw \
    RIPARR_OUTPUT_DIR=/data/media \
    RIPARR_DEFAULT_DEVICE=/dev/sr0 \
    RIPARR_LOG_LEVEL=INFO

# Volumes
VOLUME ["/data/raw", "/data/media", "/config"]

ENTRYPOINT ["riparr"]
CMD ["watch", "--gui"]
