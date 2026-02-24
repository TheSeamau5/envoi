FROM ubuntu:24.04

ARG CACHE_BUST=1
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ git curl wget cmake unzip \
    pkg-config libssl-dev libpng-dev \
    python3 python3-pip python3-pil ripgrep \
    imagemagick \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages \
    "envoi @ git+https://github.com/TheSeamau5/envoi.git@main#subdirectory=packages/envoi" \
    "httpx>=0.27.0" \
    "pydantic>=2.0.0"

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

RUN mkdir -p /workspace /environment /sandbox /tmp/upload /opt/tests

# ═══════════════════════════════════════════════════════════════════
# Reference Documentation — baked into /workspace/reference/
# ═══════════════════════════════════════════════════════════════════

RUN mkdir -p /workspace/reference

# gbctr PDF — 174-page CPU instruction timing reference by Gekkio
# Contains per-M-cycle pseudocode for every instruction + bus timing diagrams
RUN curl -sSL -o /workspace/reference/gbctr.pdf \
    "https://gekkio.fi/files/gb-docs/gbctr.pdf"

# Pan Docs — canonical community reference (markdown source)
RUN git clone --depth 1 https://github.com/gbdev/pandocs.git /tmp/pandocs && \
    mkdir -p /workspace/reference/pandocs && \
    cp /tmp/pandocs/src/*.md /workspace/reference/pandocs/ 2>/dev/null; \
    cp -r /tmp/pandocs/src/*/ /workspace/reference/pandocs/ 2>/dev/null; \
    rm -rf /tmp/pandocs

# dmgops.json — machine-readable opcode table (512 opcodes with cycle timing)
RUN git clone --depth 1 https://github.com/izik1/gbops.git /tmp/gbops && \
    cp /tmp/gbops/dmgops.json /workspace/reference/ && \
    rm -rf /tmp/gbops

# opcodes.toml — compact mnemonic reference from gb-ctr repo
RUN git clone --depth 1 https://github.com/Gekkio/gb-ctr.git /tmp/gb-ctr && \
    cp /tmp/gb-ctr/opcodes.toml /workspace/reference/ && \
    rm -rf /tmp/gb-ctr

# ═══════════════════════════════════════════════════════════════════
# Test ROM Suites
# ═══════════════════════════════════════════════════════════════════

# ─── Blargg test ROMs (precompiled) ───
RUN git clone --depth 1 https://github.com/retrio/gb-test-roms.git /opt/tests/blargg

# ─── Build wla-dx (for Mooneye) ───
RUN set -eux; \
    WLA_DX_COMMIT=89a90a56be5c2b8cf19a9afa3e1b32384ddb1a97; \
    curl -sSL "https://github.com/vhelin/wla-dx/archive/${WLA_DX_COMMIT}.tar.gz" | tar xz -C /tmp; \
    cd /tmp/wla-dx-${WLA_DX_COMMIT}; \
    cmake . && make -j$(nproc); \
    cp binaries/wla-gb binaries/wlalink /usr/local/bin/; \
    rm -rf /tmp/wla-dx-*

# ─── Mooneye test suite (build from source) ───
RUN set -eux; \
    git clone --depth 1 https://github.com/Gekkio/mooneye-test-suite.git /tmp/mooneye-src; \
    cd /tmp/mooneye-src; \
    make clean all WLA=wla-gb WLALINK=wlalink; \
    mkdir -p /opt/tests/mooneye; \
    cp -r build/* /opt/tests/mooneye/; \
    rm -rf /tmp/mooneye-src

# ─── Build RGBDS (for acid2, mealybug, SameSuite) ───
RUN set -eux; \
    git clone --depth 1 --branch v0.6.1 https://github.com/gbdev/rgbds.git /tmp/rgbds; \
    cd /tmp/rgbds; \
    make -j$(nproc); \
    make install; \
    rm -rf /tmp/rgbds

# ─── dmg-acid2 (build from source) ───
RUN set -eux; \
    git clone --recurse-submodules --depth 1 https://github.com/mattcurrie/dmg-acid2.git /tmp/dmg-acid2; \
    cd /tmp/dmg-acid2; \
    make; \
    mkdir -p /opt/tests/acid2/dmg; \
    cp build/dmg-acid2.gb /opt/tests/acid2/dmg/; \
    cp img/reference-dmg.png /opt/tests/acid2/dmg/reference.png; \
    rm -rf /tmp/dmg-acid2

# ─── cgb-acid2 (build from source) ───
RUN set -eux; \
    git clone --recurse-submodules --depth 1 https://github.com/mattcurrie/cgb-acid2.git /tmp/cgb-acid2; \
    cd /tmp/cgb-acid2; \
    make; \
    mkdir -p /opt/tests/acid2/cgb; \
    cp build/cgb-acid2.gbc /opt/tests/acid2/cgb/ || cp build/cgb-acid2.gb /opt/tests/acid2/cgb/cgb-acid2.gbc; \
    cp img/reference.png /opt/tests/acid2/cgb/reference.png; \
    rm -rf /tmp/cgb-acid2

# ─── mealybug-tearoom-tests (precompiled zip + reference PNGs) ───
RUN set -eux; \
    git clone --depth 1 https://github.com/mattcurrie/mealybug-tearoom-tests.git /tmp/mealybug; \
    mkdir -p /opt/tests/mealybug/roms /opt/tests/mealybug/expected; \
    cd /tmp/mealybug && unzip -o mealybug-tearoom-tests.zip -d /opt/tests/mealybug/roms/; \
    cp -r expected/DMG-blob /opt/tests/mealybug/expected/dmg; \
    cp -r "expected/CPU CGB C" /opt/tests/mealybug/expected/cgb; \
    rm -rf /tmp/mealybug

# ─── SameSuite (build from source) ───
RUN set -eux; \
    git clone --depth 1 https://github.com/LIJI32/SameSuite.git /tmp/samesuite; \
    cd /tmp/samesuite; \
    make; \
    mkdir -p /opt/tests/samesuite; \
    find . -name '*.gb' -exec cp --parents {} /opt/tests/samesuite/ \; ; \
    rm -rf /tmp/samesuite
