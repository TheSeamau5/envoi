#!/bin/bash
set -e

cargo build --release
cp target/release/c_compiler ./cc
