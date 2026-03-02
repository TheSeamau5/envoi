FROM ubuntu:24.04

ARG CACHE_BUST=1
ARG IMPL_LANG=rust
ARG TASK_LANG=en
ARG MILESTONE=M0
ENV DEBIAN_FRONTEND=noninteractive
ENV ENVOI_IMPL_LANG=${IMPL_LANG}
ENV ENVOI_TASK_LANG=${TASK_LANG}
ENV ENVOI_MILESTONE=${MILESTONE}

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ git curl wget cmake unzip \
    bison flex \
    pkg-config libssl-dev libpng-dev \
    python3 python3-pip python3-pil ripgrep \
    imagemagick \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN uv pip install --system \
    "envoi-ai @ git+https://github.com/TheSeamau5/envoi.git@main#subdirectory=packages/envoi" \
    "httpx>=0.27.0" \
    "pydantic>=2.0.0"

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

RUN mkdir -p /workspace /environment /sandbox /tmp/upload /opt/tests

# Workspace files: AGENTS.md for agent onboarding
COPY workspace/AGENTS.md /workspace/AGENTS.md

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

# SameBoy — reference emulator source (C). Read-only reference for understanding
# hardware behavior. The agent may read this code but MUST NOT link/FFI to it.
RUN git clone --depth 1 https://github.com/LIJI32/SameBoy.git /workspace/reference/sameboy

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

# ─── Breakpoint control ROMs (anti-cheat guard for exit-code semantics) ───
RUN set -eux; \
    mkdir -p /tmp/breakpoint-guard /opt/tests/controls; \
    printf '%s\n' \
    'SECTION "Entry", ROM0[$0100]' \
    '    jp Start' \
    '    ds $0150 - @, 0' \
    '' \
    'SECTION "Code", ROM0[$0150]' \
    'Start:' \
    '    ld b, $00' \
    '    ld c, $00' \
    '    ld d, $00' \
    '    ld e, $00' \
    '    ld h, $00' \
    '    ld l, $00' \
    '    db $40' \
    'Hang:' \
    '    jr Hang' \
    > /tmp/breakpoint-guard/fail.asm; \
    printf '%s\n' \
    'SECTION "Entry", ROM0[$0100]' \
    '    jp Start' \
    '    ds $0150 - @, 0' \
    '' \
    'SECTION "Code", ROM0[$0150]' \
    'Start:' \
    '    ld b, $03' \
    '    ld c, $05' \
    '    ld d, $08' \
    '    ld e, $0D' \
    '    ld h, $15' \
    '    ld l, $22' \
    '    db $40' \
    'Hang:' \
    '    jr Hang' \
    > /tmp/breakpoint-guard/pass.asm; \
    cd /tmp/breakpoint-guard; \
    rgbasm -o fail.o fail.asm; \
    rgblink -o breakpoint_fail.gb fail.o; \
    rgbfix -v -p 255 breakpoint_fail.gb; \
    rgbasm -o pass.o pass.asm; \
    rgblink -o breakpoint_pass.gb pass.o; \
    rgbfix -v -p 255 breakpoint_pass.gb; \
    cp breakpoint_fail.gb breakpoint_pass.gb /opt/tests/controls/; \
    rm -rf /tmp/breakpoint-guard

# ─── dmg-acid2 (build from source) ───
RUN set -eux; \
    git clone --recurse-submodules --depth 1 https://github.com/mattcurrie/dmg-acid2.git /tmp/dmg-acid2; \
    cd /tmp/dmg-acid2; \
    sed -E -i 's/^[[:space:]]*HARDWARE_INC[[:space:]]+SET[[:space:]]+1[[:space:]]*$/DEF HARDWARE_INC EQU 1/' mgblib/src/hardware.inc; \
    grep -Eq '^DEF HARDWARE_INC EQU 1$' mgblib/src/hardware.inc; \
    make; \
    mkdir -p /opt/tests/acid2/dmg; \
    cp build/dmg-acid2.gb /opt/tests/acid2/dmg/; \
    cp img/reference-dmg.png /opt/tests/acid2/dmg/reference.png; \
    rm -rf /tmp/dmg-acid2

# ─── cgb-acid2 (build from source) ───
RUN set -eux; \
    git clone --recurse-submodules --depth 1 https://github.com/mattcurrie/cgb-acid2.git /tmp/cgb-acid2; \
    cd /tmp/cgb-acid2; \
    sed -E -i 's/^[[:space:]]*HARDWARE_INC[[:space:]]+SET[[:space:]]+1[[:space:]]*$/DEF HARDWARE_INC EQU 1/' mgblib/src/hardware.inc; \
    grep -Eq '^DEF HARDWARE_INC EQU 1$' mgblib/src/hardware.inc; \
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
