#!/bin/sh
# Runs inside each dedicated cgroup. 200ms target interval, actual wall timestamps retained.
# Observer shell/awk/stat overhead is charged to that role. RSS is sampled; memory.peak is cgroup peak.
set -eu
stage_sample_stop=${1:-/run/measurement-stop}
while test ! -e "$stage_sample_stop"; do
  stage_current=$(cat /sys/fs/cgroup/memory.current)
  stage_peak=$(cat /sys/fs/cgroup/memory.peak)
  stage_swap=$(cat /sys/fs/cgroup/memory.swap.current)
  stage_cpu=$(awk '$1=="usage_usec" {print $2}' /sys/fs/cgroup/cpu.stat)
  stage_rss=$(awk '/VmRSS:/ {total+=$2} END {print total+0}' /proc/[0-9]*/status 2>/dev/null || true)
  stage_main_rss=$(awk '/VmRSS:/ {print $2}' /proc/1/status)
  stage_deleted=$(
    for stage_fd in /proc/[0-9]*/fd/*; do
      case "$(readlink "$stage_fd" 2>/dev/null || true)" in
        *' (deleted)') stat -Lc '%d:%i %b' "$stage_fd" 2>/dev/null || true;;
      esac
    done | awk '!seen[$1]++ {blocks+=$2} END {print blocks*512+0}'
  )
  stage_events=$(tr '\n' ',' < /sys/fs/cgroup/memory.events)
  printf 'at=%s memory=%s peak=%s swap=%s cpu_usec=%s rss_sum_kib=%s main_rss_kib=%s deleted_fd_allocated_bytes=%s events=%s\n' \
    "$(date +%s.%N)" "$stage_current" "$stage_peak" "$stage_swap" "$stage_cpu" "$stage_rss" "${stage_main_rss:-0}" "$stage_deleted" "$stage_events"
  sleep .2
done
df -k /tmp
cat /sys/fs/cgroup/memory.events
cat /sys/fs/cgroup/cpu.stat
