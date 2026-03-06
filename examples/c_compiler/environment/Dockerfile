FROM ubuntu:24.04

ARG CACHE_BUST=2
ARG TARGET=x86_64-linux
ARG IMPL_LANG=rust
ARG TASK_LANG=en
ARG MILESTONE=M0
ENV DEBIAN_FRONTEND=noninteractive
ENV ENVOI_TARGET=${TARGET}
ENV ENVOI_IMPL_LANG=${IMPL_LANG}
ENV ENVOI_TASK_LANG=${TASK_LANG}
ENV ENVOI_MILESTONE=${MILESTONE}

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ clang git curl wget \
    pkg-config libssl-dev python3 python3-pip ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN uv pip install --system \
    "envoi-ai @ git+https://github.com/TheSeamau5/envoi.git@main#subdirectory=packages/envoi" \
    "httpx>=0.27.0" \
    "pydantic>=2.0.0"

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

RUN mkdir -p /workspace /environment /sandbox /tmp/upload /opt/tests

# Workspace files: AGENTS.md for agent onboarding
COPY workspace/AGENTS.md /workspace/AGENTS.md
COPY workspace/reference/ /workspace/reference/

# Reference PDFs — fetched at build time so /workspace/reference/ contains
# real deep-reference material in addition to the checked-in markdown summaries.
RUN set -eux; \
    curl -fsSL -o /workspace/reference/c23-n3220.pdf \
        "https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3220.pdf"; \
    head -c 5 /workspace/reference/c23-n3220.pdf | grep -q '^%PDF-'; \
    curl -fsSL -o /workspace/reference/x86_64-SysV-psABI.pdf \
        "https://refspecs.linuxbase.org/elf/x86_64-SysV-psABI.pdf"; \
    head -c 5 /workspace/reference/x86_64-SysV-psABI.pdf | grep -q '^%PDF-'

# Bake fixtures into the image to avoid per-run sync costs.
RUN set -eux; \
    git clone --depth 1 https://github.com/c-testsuite/c-testsuite.git /opt/tests/c-testsuite; \
    git clone --depth 1 https://github.com/nlsandler/writing-a-c-compiler-tests.git /opt/tests/wacct; \
    mkdir -p /opt/tests/llvm-test-suite; \
    cd /opt/tests/llvm-test-suite; \
    git init; \
    git remote add origin https://github.com/llvm/llvm-test-suite.git; \
    git config core.sparseCheckout true; \
    echo "SingleSource/Regression/C/gcc-c-torture/execute/" > .git/info/sparse-checkout; \
    git pull --depth 1 origin main
