"""Summarize retained stage samples without treating sampled RSS as an exact peak."""
import json
from pathlib import Path
import re
import sys


def summarize(samples):
    if not samples:
        return {'samples': 0}
    elapsed = samples[-1]['at'] - samples[0]['at']
    gaps = [b['at'] - a['at'] for a, b in zip(samples, samples[1:])]
    return {
        'samples': len(samples), 'elapsed_s': elapsed,
        'max_sample_gap_s': max(gaps) if gaps else None,
        'sampled_cgroup_current_max_bytes': max(s['memory'] for s in samples),
        'observed_lifetime_cgroup_peak_bytes': max(s['peak'] for s in samples),
        'sampled_main_pid1_rss_max_kib': max(s['main_rss_kib'] for s in samples),
        'sampled_sum_process_rss_max_kib_shared_pages_counted_repeatedly': max(s['rss_sum_kib'] for s in samples),
        'sampled_deleted_fd_allocated_max_bytes': max(s['deleted_fd_allocated_bytes'] for s in samples),
        'sampled_swap_max_bytes': max(s.get('swap', 0) for s in samples),
        'cpu_usage_delta_usec': samples[-1]['cpu_usec'] - samples[0]['cpu_usec'],
        'mean_cpu_one_core_percent': (samples[-1]['cpu_usec'] - samples[0]['cpu_usec']) / elapsed / 10000 if elapsed > 0 else None,
        'oom_events_max_observed': max(s.get('oom', 0) for s in samples),
        'oom_kill_events_max_observed': max(s.get('oom_kill', 0) for s in samples),
    }


def run(directory):
    records = [json.loads(line) for line in (directory / 'capacity.jsonl').read_text().splitlines()]
    result = {'roles': {}, 'cells': []}
    role_samples = {}
    for role in ('python', 'go', 'next', 'load'):
        samples = []
        for line in (directory / f'{role}-samples.log').read_text().splitlines():
            if not line.startswith('at='):
                continue
            values = {key: float(value) for key, value in re.findall(r'(?:^| )(\w+)=([0-9.eE+-]+)', line)}
            for key in ('oom', 'oom_kill'):
                match = re.search(r'(?:events=|,)' + key + r' (\d+)', line)
                if match:
                    values[key] = int(match[1])
            samples.append(values)
        role_samples[role] = samples
        result['roles'][role] = summarize(samples)
        inspect = directory / f'{role}-inspect.json'
        if inspect.exists():
            info = json.loads(inspect.read_text())[0]
            result['roles'][role]['docker_state'] = info['State']
            result['roles'][role]['memory_limit_bytes'] = info['HostConfig']['Memory']
            result['roles'][role]['swap_total_limit_bytes'] = info['HostConfig']['MemorySwap']
            result['roles'][role]['nano_cpus'] = info['HostConfig']['NanoCpus']
    for row in records:
        if row.get('measured_start_unix'):
            cell = {'scenario': row['scenario'], 'concurrency': row.get('concurrency'),
                    'conversation': row.get('conversation'), 'roles': {}}
            for role, samples in role_samples.items():
                selected = [s for s in samples if row['measured_start_unix'] <= s['at'] <= row['at_unix']]
                cell['roles'][role] = summarize(selected)
            result['cells'].append(cell)
    (directory / 'resource-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    run(Path(sys.argv[1]))
