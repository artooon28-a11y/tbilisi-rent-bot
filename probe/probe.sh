#!/usr/bin/env bash
# Temporary probe: compares ss.ge listing pages with different params from GitHub's servers.
set -u
mkdir -p probe/out
python3 probe/probe3.py > probe/out/summary.txt 2>&1
echo done
