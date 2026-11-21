#!/bin/bash
set -euo pipefail
export ELAN_HOME=/x/elan HOME=/x/home XDG_CACHE_HOME=/x/cache
mkdir -p $HOME $XDG_CACHE_HOME
date -u +"stage start %FT%TZ"
apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates zstd >/dev/null
curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -o /x/elan-init.sh
sh /x/elan-init.sh -y --default-toolchain none --no-modify-path
export PATH=$ELAN_HOME/bin:$PATH
elan toolchain install leanprover/lean4:v4.33.1
rm -rf /x/mathlib4
git init -q /x/mathlib4 && cd /x/mathlib4
git remote add origin https://github.com/leanprover-community/mathlib4.git
git fetch -q --depth 1 origin 0df444a360eaa60ab8c11dca51a86af692955474
git checkout -q FETCH_HEAD
git rev-parse HEAD; cat lean-toolchain
date -u +"cache get start %FT%TZ"
lake exe cache get 2>&1 | tee /x/cache-get.log | tail -40
date -u +"stage done %FT%TZ"
chown -R "$HOST_UID:$HOST_GID" /x
