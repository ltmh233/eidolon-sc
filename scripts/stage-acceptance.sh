#!/bin/sh
# Local synthetic only. All language execution stays inside dedicated Docker containers.
# Usage: ARTIFACTS=/absolute/copied/artifacts sh scripts/stage-acceptance.sh start loopback S unique-run
# Then: ... functional unique-run; ... capacity unique-run; ... receipt unique-run; ... stop unique-run
set -eu
stage_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
stage_artifacts=${ARTIFACTS:?Set ARTIFACTS to the exact copied venv/core/next directory}
stage_evidence=$stage_root/.scratch/go-migration-stage-close
stage_prefix=eidolon-stage-close-20261030
stage_action=${1:?action required}
stage_run=${2:-}
# Narrow mount preflight. Neither backend/ nor the repository root is an allowed mount.
stage_artifacts=$(CDPATH= cd -- "$stage_artifacts" && pwd)
case "$stage_artifacts" in /private/tmp/eidolon-stage-close-20261030-*) ;; *) echo 'artifact path must be the dedicated /private/tmp directory' >&2; exit 2;; esac
for stage_input in backend/src backend/tests characters core/packs core/testdata; do
  test -d "$stage_root/$stage_input"
  test ! -L "$stage_root/$stage_input"
  stage_sensitive=$(find "$stage_root/$stage_input" \( -name '.env' -o -name '.env.*' -o -name '.ssh' -o -name '*.sock' \) -print -quit)
  test -z "$stage_sensitive" || { echo 'sensitive path in allowed input; refusing mount' >&2; exit 2; }
done

# The Docker CLI stays on the host. No Docker socket is exposed to a measured role.
# A child OOM emits a container OOM event even if PID1 survives and HTTP still returns200.
stage_oom_event_pid=''
stage_oom_reader_pid=''
stage_samplers=''
stage_current_sampler_stop=''
stage_cleanup() {
  if test -n "$stage_current_sampler_stop"; then touch "$stage_current_sampler_stop"; fi
  for stage_sampler in $stage_samplers; do wait "$stage_sampler" 2>/dev/null || true; done
  stage_samplers=''
  stage_watch_stop
}
stage_interrupt() {
  printf '%s\n' "$1" > "$stage_evidence/$stage_run/operator-interrupt"
  stage_cleanup
}
stage_watch_start() {
  stage_watch_label=$1; shift
  stage_watch_fifo="$stage_evidence/$stage_run/$stage_watch_label-oom.fifo"
  stage_watch_requested="$stage_evidence/$stage_run/$stage_watch_label-watch-stop-requested"
  mkfifo "$stage_watch_fifo"
  docker events --since "$stage_started" --filter event=oom --format '{{json .}}' "$@" > "$stage_watch_fifo" &
  stage_oom_event_pid=$!
  (
    while IFS= read -r stage_oom_line; do
      printf '%s\n' "$stage_oom_line" >> "$stage_evidence/$stage_run/$stage_watch_label-live-oom.jsonl"
      touch "$stage_evidence/$stage_run/oom-stop"
    done < "$stage_watch_fifo"
    test -e "$stage_watch_requested" || touch "$stage_evidence/$stage_run/oom-watch-failed"
  ) &
  stage_oom_reader_pid=$!
  trap 'stage_cleanup' EXIT
  trap 'stage_interrupt INT; exit 130' INT
  trap 'stage_interrupt TERM; exit 143' TERM
  sleep .1
  if ! kill -0 "$stage_oom_event_pid" 2>/dev/null; then
    touch "$stage_evidence/$stage_run/oom-watch-failed"
    return 1
  fi
}
stage_watch_stop() {
  if test -n "$stage_oom_event_pid"; then
    touch "$stage_watch_requested"
    kill "$stage_oom_event_pid" 2>/dev/null || true
    wait "$stage_oom_event_pid" 2>/dev/null || true
    wait "$stage_oom_reader_pid" 2>/dev/null || true
    stage_oom_event_pid=''
    stage_oom_reader_pid=''
  fi
}
stage_safety_exit() {
  if test -e "$stage_evidence/$stage_run/oom-stop" || test -e "$stage_evidence/$stage_run/oom-watch-failed" || test -e "$stage_evidence/$stage_run/operator-interrupt"; then
    printf '86\n' > "$stage_evidence/$stage_run/$1"
    echo 'OOM evidence, observer failure or operator interrupt: remaining load safety-stopped' >&2
    return 86
  fi
}
stage_check_lifecycle() {
  stage_check_phase=$1; shift
  # Existing evidence rejects before Docker access (also usable by the no-socket proof).
  stage_safety_exit "$stage_check_phase-admission.exit" || return $?
  for stage_check_container in "$@"; do
    stage_check_file="$stage_evidence/$stage_run/$stage_check_phase-entry-$stage_check_container-state.json"
    if ! docker inspect --format '{{json .State}}' "$stage_check_container" > "$stage_check_file" 2> "$stage_check_file.err"; then
      printf 'entry state inspection failed\n' > "$stage_evidence/$stage_run/oom-watch-failed"
      stage_safety_exit "$stage_check_phase-admission.exit" || return $?
    fi
    if grep -q '"OOMKilled":[[:space:]]*true' "$stage_check_file"; then
      touch "$stage_evidence/$stage_run/oom-stop"
      stage_safety_exit "$stage_check_phase-admission.exit" || return $?
    fi
  done
}

case "$stage_action" in
start)
  stage_topology=${2:?loopback or bridge}; stage_size=${3:?S M or L}; stage_run=${4:?unique run id}
  case "$stage_topology:$stage_size" in loopback:S|loopback:M|loopback:L|bridge:S|bridge:M|bridge:L) ;; *) exit 2;; esac
  case "$stage_run" in *[!a-z0-9-]*|'') exit 2;; esac
  test ! -e "$stage_evidence/$stage_run"
  # Fresh low-cost probe, retained. No unrelated container may run during measurement.
  test -z "$(docker ps -q)"
  docker info --format '{{json .}}' > "$stage_evidence/logs/$stage_run-docker-info.json"
  stage_available=$(docker run --name "$stage_prefix-$stage_run-probe" --network none --memory 32m --memory-swap 32m --cpus .1 --pids-limit 32 ghcr.io/astral-sh/uv:python3.11-bookworm-slim sh -c 'df -k /tmp >&2; awk '\''/MemAvailable:/ {printf "%.0f\n", $2*1024}'\'' /proc/meminfo' 2> "$stage_evidence/logs/$stage_run-disk-before.txt")
  stage_total=$(docker info --format '{{.MemTotal}}')
  stage_reserve=$((stage_total / 4)); test "$stage_reserve" -ge 1073741824 || stage_reserve=1073741824
  # awk may use scientific notation, normalize without running a host language runtime.
  stage_available=$(printf '%.0f' "$stage_available")
  printf 'available=%s total=%s reserve=%s group=2248146944\n' "$stage_available" "$stage_total" "$stage_reserve" > "$stage_evidence/logs/$stage_run-admission.txt"
  test $((stage_available - 2248146944)) -ge "$stage_reserve"
  docker run --name "$stage_prefix-$stage_run-release" --network none --memory 512m --memory-swap 512m --cpus 1 --pids-limit 128 \
    -v "$stage_artifacts/venv:/work/backend/.venv:ro" -v "$stage_artifacts/core:/artifacts/core:ro" \
    -v "$stage_root/backend/src:/source/backend/src:ro" -v "$stage_root/backend/tests:/source/backend/tests:ro" -v "$stage_root/backend/pyproject.toml:/source/backend/pyproject.toml:ro" -v "$stage_root/characters:/source/characters:ro" -v "$stage_root/core/packs:/source/core/packs:ro" -v "$stage_root/core/testdata:/source/core/testdata:ro" \
    -v "$stage_evidence:/evidence" -e PYTHONPATH=/source/backend/src:/source/backend -e PYTHONDONTWRITEBYTECODE=1 -e EIDOLON_CORE_BINARY=/artifacts/core \
    ghcr.io/astral-sh/uv:python3.11-bookworm-slim /work/backend/.venv/bin/python -m tests.stage_acceptance release --output "/evidence/$stage_run" \
    > "$stage_evidence/logs/$stage_run-release.log" 2>&1
  if test "$stage_topology" = bridge; then
    docker network create --internal "$stage_prefix-$stage_run-net" > "$stage_evidence/$stage_run/network-id"
    stage_python_network="$stage_prefix-$stage_run-net"; stage_legacy=http://python:8000; stage_go_url=http://go:8081; stage_next_url=http://next:3000
  else
    stage_python_network=none; stage_legacy=http://127.0.0.1:8000; stage_go_url=http://127.0.0.1:8081; stage_next_url=http://127.0.0.1:3000
  fi
  printf '{"python":"%s","go":"%s","next":"%s","topology":"%s","size":"%s"}\n' "$stage_legacy" "$stage_go_url" "$stage_next_url" "$stage_topology" "$stage_size" > "$stage_evidence/$stage_run/endpoints.json"
  docker run -d --name "$stage_prefix-$stage_run-python" --network "$stage_python_network" --hostname python --memory 1g --memory-swap 1g --cpus 2 --pids-limit 256 \
    -v "$stage_artifacts/venv:/work/backend/.venv:ro" -v "$stage_root/backend/src:/source/backend/src:ro" -v "$stage_root/backend/tests:/source/backend/tests:ro" -v "$stage_root/backend/pyproject.toml:/source/backend/pyproject.toml:ro" -v "$stage_root/characters:/source/characters:ro" \
    -v "$stage_evidence/$stage_run:/run" -e PYTHONPATH=/source/backend/src:/source/backend -e PYTHONDONTWRITEBYTECODE=1 \
    -e EIDOLON_TEST_HOST=0.0.0.0 -e EIDOLON_TEST_PORT=8000 -e "EIDOLON_STAGE_SIZE=$stage_size" -e "EIDOLON_STAGE_FAULTS=${STAGE_FAULTS:-0}" \
    ghcr.io/astral-sh/uv:python3.11-bookworm-slim /work/backend/.venv/bin/python -m tests.three_process_backend /run /run/content.json /run/display.json null
  if test "$stage_topology" = loopback; then stage_role_network="container:$stage_prefix-$stage_run-python"; else stage_role_network="$stage_python_network"; fi
  docker run -d --name "$stage_prefix-$stage_run-go" --network "$stage_role_network" --memory 96m --memory-swap 96m --cpus 2 --pids-limit 128 \
    -v "$stage_artifacts/core:/artifacts/core:ro" -v "$stage_root/core/packs:/packs:ro" -v "$stage_root/core/testdata/packs:/test-packs:ro" -v "$stage_evidence/$stage_run:/run:ro" \
    -e GOMEMLIMIT=64MiB ghcr.io/astral-sh/uv:python3.11-bookworm-slim /artifacts/core -listen 0.0.0.0:8081 -legacy "$stage_legacy" -packs /packs,/test-packs \
    -content-snapshot /run/content.json -catalog-display /run/display.json -affinity-token-file /run/core-token -stats-token-file /run/core-token \
    -timeline-token-file /run/core-token -summaries-token-file /run/core-token -sc-create-token-file /run/create-token -achievements-token-file /run/core-token -gifts-panel-token-file /run/panel-token
  docker run -d --name "$stage_prefix-$stage_run-next" --network "$stage_role_network" --memory 512m --memory-swap 512m --cpus 1 --pids-limit 128 \
    -v "$stage_artifacts/next:/next:ro" -v "$stage_evidence/$stage_run:/run:ro" -e HOSTNAME=0.0.0.0 -e PORT=3000 -e "BACKEND_URL=$stage_go_url" \
    -w /next node:22-bookworm node server.js
  docker run -d --name "$stage_prefix-$stage_run-load" --network "$stage_role_network" --memory 512m --memory-swap 512m --cpus 1 --pids-limit 128 \
    -v "$stage_artifacts/venv:/work/backend/.venv:ro" -v "$stage_artifacts/core:/artifacts/core:ro" -v "$stage_root/backend/src:/source/backend/src:ro" -v "$stage_root/backend/tests:/source/backend/tests:ro" -v "$stage_root/backend/pyproject.toml:/source/backend/pyproject.toml:ro" \
    -v "$stage_artifacts/node:/usr/local/bin/node:ro" \
    -v "$stage_root/characters:/source/characters:ro" -v "$stage_root/core/packs:/source/core/packs:ro" -v "$stage_root/core/testdata:/source/core/testdata:ro" \
    -v "$stage_evidence/$stage_run:/run" -e PYTHONPATH=/source/backend/src:/source/backend -e PYTHONDONTWRITEBYTECODE=1 \
    -e EIDOLON_STAGE_CHAIN=/run -e EIDOLON_CORE_BINARY=/artifacts/core -e EIDOLON_NEXT_SERVER=/fixed-next/server.js \
    -w /source/backend ghcr.io/astral-sh/uv:python3.11-bookworm-slim sleep infinity
  if test "$stage_topology" = bridge; then
    # Explicit DNS aliases; container names remain unique and attributable.
    docker network disconnect "$stage_python_network" "$stage_prefix-$stage_run-python"
    docker network connect --alias python "$stage_python_network" "$stage_prefix-$stage_run-python"
    docker network disconnect "$stage_python_network" "$stage_prefix-$stage_run-go"
    docker network connect --alias go "$stage_python_network" "$stage_prefix-$stage_run-go"
    docker network disconnect "$stage_python_network" "$stage_prefix-$stage_run-next"
    docker network connect --alias next "$stage_python_network" "$stage_prefix-$stage_run-next"
  fi
  for stage_role in python go next load; do
    docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "$stage_prefix-$stage_run-$stage_role" > "$stage_evidence/$stage_run/$stage_role-mounts.txt"
    while IFS= read -r stage_source; do
      case "$stage_source" in
        "$stage_root/backend/src"|"$stage_root/backend/tests"|"$stage_root/backend/pyproject.toml"|"$stage_root/characters"|"$stage_root/core/packs"|"$stage_root/core/testdata"|"$stage_root/core/testdata/packs"|"$stage_artifacts/venv"|"$stage_artifacts/core"|"$stage_artifacts/node"|"$stage_artifacts/next"|"$stage_evidence/$stage_run"|'') ;;
        *) echo 'unexpected actual mount; stopping dedicated group' >&2; docker stop "$stage_prefix-$stage_run-python" "$stage_prefix-$stage_run-go" "$stage_prefix-$stage_run-next" "$stage_prefix-$stage_run-load"; exit 2;;
      esac
    done < "$stage_evidence/$stage_run/$stage_role-mounts.txt"
  done
  ;;
functional)
  test ! -e "$stage_evidence/$stage_run/pytest-functional"
  stage_started=$(date +%s)
  stage_check_lifecycle functional "$stage_prefix-$stage_run-python" "$stage_prefix-$stage_run-go" "$stage_prefix-$stage_run-next" "$stage_prefix-$stage_run-load"
  stage_samplers=''
  stage_watch_start functional --filter "container=$stage_prefix-$stage_run-python" --filter "container=$stage_prefix-$stage_run-go" --filter "container=$stage_prefix-$stage_run-next" --filter "container=$stage_prefix-$stage_run-load"
  if grep -q '"size":"L"' "$stage_evidence/$stage_run/endpoints.json"; then
    stage_current_sampler_stop="$stage_evidence/$stage_run/functional-measurement-stop"
    for stage_role in python go next load; do
      docker exec -i "$stage_prefix-$stage_run-$stage_role" sh -s -- /run/functional-measurement-stop < "$stage_root/scripts/stage-sample.sh" > "$stage_evidence/$stage_run/functional-$stage_role-samples.log" 2>&1 &
      stage_samplers="$stage_samplers $!"
    done
  fi
  stage_result=0
  docker exec "$stage_prefix-$stage_run-load" sh -c 'test ! -e /run/pytest-functional && PYTHONUNBUFFERED=1 /work/backend/.venv/bin/pytest -q -s -p no:cacheprovider --basetemp=/run/pytest-functional "tests/test_three_process.py::test_real_three_process_chain[None]" > /run/functional.log 2>&1; result=$?; echo "$result" > /run/functional.exit; exit "$result"' || stage_result=$?
  stage_cleanup
  stage_ended=$(( $(date +%s) + 1 ))
  for stage_role in python go next load; do
    docker events --since "$stage_started" --until "$stage_ended" --filter "container=$stage_prefix-$stage_run-$stage_role" --filter event=oom --format '{{json .}}' > "$stage_evidence/$stage_run/functional-$stage_role-oom-events.jsonl"
    test ! -s "$stage_evidence/$stage_run/functional-$stage_role-oom-events.jsonl" || touch "$stage_evidence/$stage_run/oom-stop"
  done
  stage_safety_exit functional-command.exit || exit $?
  printf '%s\n' "$stage_result" > "$stage_evidence/$stage_run/functional-command.exit"
  exit "$stage_result"
  ;;
faults)
  test "$(cat "$stage_evidence/$stage_run/functional.exit")" = 0
  stage_check_lifecycle faults "$stage_prefix-$stage_run-python" "$stage_prefix-$stage_run-go" "$stage_prefix-$stage_run-next" "$stage_prefix-$stage_run-load"
  test ! -e "$stage_evidence/$stage_run/faults.jsonl"
  docker exec "$stage_prefix-$stage_run-load" /work/backend/.venv/bin/python -m tests.stage_faults /run > "$stage_evidence/$stage_run/faults.log" 2>&1
  ;;
capacity)
  test "$(cat "$stage_evidence/$stage_run/functional.exit")" = 0
  stage_started=$(date +%s)
  stage_check_lifecycle capacity "$stage_prefix-$stage_run-python" "$stage_prefix-$stage_run-go" "$stage_prefix-$stage_run-next" "$stage_prefix-$stage_run-load"
  test ! -e "$stage_evidence/$stage_run/capacity.jsonl"
  stage_samplers=''
  stage_watch_start capacity --filter "container=$stage_prefix-$stage_run-python" --filter "container=$stage_prefix-$stage_run-go" --filter "container=$stage_prefix-$stage_run-next" --filter "container=$stage_prefix-$stage_run-load"
  stage_current_sampler_stop="$stage_evidence/$stage_run/measurement-stop"
  for stage_role in python go next load; do
    docker exec -i "$stage_prefix-$stage_run-$stage_role" sh < "$stage_root/scripts/stage-sample.sh" > "$stage_evidence/$stage_run/$stage_role-samples.log" 2>&1 &
    stage_samplers="$stage_samplers $!"
  done
  stage_result=0
  docker exec "$stage_prefix-$stage_run-load" /work/backend/.venv/bin/python -m tests.stage_capacity /run > "$stage_evidence/$stage_run/capacity.log" 2>&1 || stage_result=$?
  stage_cleanup
  printf '%s\n' "$stage_result" > "$stage_evidence/$stage_run/capacity-command.exit"
  stage_ended=$(( $(date +%s) + 1 ))
  for stage_role in python go next load; do
    docker events --since "$stage_started" --until "$stage_ended" --filter "container=$stage_prefix-$stage_run-$stage_role" --filter event=oom --format '{{json .}}' > "$stage_evidence/$stage_run/$stage_role-oom-events.jsonl"
    test ! -s "$stage_evidence/$stage_run/$stage_role-oom-events.jsonl" || touch "$stage_evidence/$stage_run/oom-stop"
  done
  stage_safety_exit capacity-command.exit || exit $?
  exit "$stage_result"
  ;;
watch-probe)
  # Dedicated low-memory child-OOM experiment; run only while measured groups are stopped.
  stage_started=$(date +%s)
  stage_watch_start probe --filter "container=$stage_prefix-$stage_run"
  if test "${SIGNAL_PROBE:-0}" = 1; then
    stage_current_sampler_stop="$stage_evidence/$stage_run/signal-measurement-stop"
    docker exec -i "$stage_prefix-$stage_run" sh -s -- /run/signal-measurement-stop < "$stage_root/scripts/stage-sample.sh" > "$stage_evidence/$stage_run/signal-samples.log" 2>&1 &
    stage_samplers=$!
  fi
  while test ! -e "$stage_evidence/$stage_run/probe-monitor-stop"; do sleep .1; done
  stage_cleanup
  ;;
check-probe-state)
  stage_probe_name=${3:?owned probe name required}
  case "$stage_probe_name" in *[!a-z0-9-]*|'') exit 2;; esac
  stage_check_lifecycle lifecycle "$stage_prefix-$stage_probe_name"
  ;;
receipt)
  for stage_role in python go next load; do
    docker inspect "$stage_prefix-$stage_run-$stage_role" > "$stage_evidence/$stage_run/$stage_role-inspect.json"
    docker logs "$stage_prefix-$stage_run-$stage_role" > "$stage_evidence/$stage_run/$stage_role.log" 2>&1
  done
  docker system df > "$stage_evidence/$stage_run/docker-disk.txt"
  docker ps -a -s --filter "name=$stage_prefix-$stage_run-" > "$stage_evidence/$stage_run/container-layer-sizes.txt"
  ;;
stop)
  for stage_role in load next go python; do docker stop "$stage_prefix-$stage_run-$stage_role"; done
  ;;
*) echo 'actions: start, functional, faults, capacity, receipt, stop' >&2; exit 2;;
esac
