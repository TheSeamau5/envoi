# E2B template image — mirrors the Modal sandbox_image in runner.py.
#
# Build once with:
#   cd sandbox/e2b && e2b template build --name envoi-trace --dockerfile e2b.Dockerfile
#
# The resulting template ID goes in E2B_TEMPLATE (defaults to "envoi-trace").

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# System packages (same as sandbox_image.apt_install in runner.py)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        clang \
        git \
        curl \
        wget \
        pkg-config \
        libssl-dev \
        python3 \
        python3-pip \
        python3-venv \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Python packages (same as sandbox_image.pip_install in runner.py)
RUN pip3 install --break-system-packages \
        "envoi @ git+https://github.com/TheSeamau5/envoi.git" \
        "httpx>=0.27.0" \
        "opencode-ai>=0.1.0a36" \
        "pypdfium2>=4.30.0" \
        "Pillow>=10.0.0" \
        "pydantic>=2.0.0" \
        "mcp>=1.0.0"

# Rust toolchain (same as sandbox_image.run_commands in runner.py)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Working directories expected by the agent runtime
RUN mkdir -p /workspace /environment /sandbox /tmp/upload
