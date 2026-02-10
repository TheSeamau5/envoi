FROM --platform=linux/amd64 ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    build-essential \
    ca-certificates \
    clang \
    curl \
    gcc \
    git \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY envoi /app/envoi
RUN pip3 install --no-cache-dir --break-system-packages .

RUN git clone --depth 1 https://github.com/c-testsuite/c-testsuite.git /opt/tests/c-testsuite
RUN git clone --depth 1 https://github.com/nlsandler/writing-a-c-compiler-tests.git /opt/tests/wacct

# Sparse checkout: only GCC C Torture execute tests
RUN mkdir -p /opt/tests/llvm-test-suite && cd /opt/tests/llvm-test-suite \
    && git init \
    && git remote add origin https://github.com/llvm/llvm-test-suite.git \
    && git config core.sparseCheckout true \
    && echo "SingleSource/Regression/C/gcc-c-torture/execute/" > .git/info/sparse-checkout \
    && git pull --depth 1 origin main

COPY examples/c_compiler/author /environment
WORKDIR /environment
ENV PYTHONPATH="/environment"

EXPOSE 8000
CMD ["python3", "-m", "envoi.runtime", "--file", "/environment/main.py", "--port", "8000"]
