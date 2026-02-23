FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ clang git curl wget \
    pkg-config libssl-dev python3 python3-pip ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages \
    "envoi @ git+https://github.com/TheSeamau5/envoi.git" \
    "httpx>=0.27.0" \
    "pydantic>=2.0.0" \
    "mcp>=1.0.0"

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

RUN mkdir -p /workspace /environment /sandbox /tmp/upload
