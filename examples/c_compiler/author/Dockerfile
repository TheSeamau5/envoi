FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    build-essential \
    ca-certificates \
    curl \
    gcc \
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

COPY examples/c_compiler/author /environment
WORKDIR /environment

EXPOSE 8000
CMD ["python3", "-m", "envoi.runtime", "--file", "/environment/c_compiler.py", "--port", "8000"]
