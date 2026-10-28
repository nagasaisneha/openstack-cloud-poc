"""Source-backed event intervals and counter deltas, separate from money.

Billing intervals use provider-confirmed lifecycle completion timestamps.
CPU seconds include domain-process overhead; network bytes are combined
interface traffic, not internet egress. Unknown history is never zero-filled.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import socket

from .billing import BillingError, BillingErrorCode, UsageBatch
from .usage import UsageProvenance, UsageSample

SOURCE = 'openstack:provider-events+libvirt-readonly'
START_ACTIONS = {'create', 'start', 'resume', 'unpause', 'unshelve'}
STOP_ACTIONS = {'stop', 'suspend', 'pause', 'shelve', 'shelve_offload', 'delete', 'soft_delete'}
UNSUPPORTED_ACTIONS = {'resize', 'confirmResize', 'revertResize', 'rebuild', 'reboot', 'migrate', 'live-migration', 'restore'}


def timestamp(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        raise ValueError('source_timestamp_missing')
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def seconds(start, end):
    difference = end - start
    return Decimal(difference.days * 86400 + difference.seconds) + Decimal(difference.microseconds) / Decimal(1000000)


def interval_seconds(begin, finish, start, end):
    left, right = max(timestamp(begin), start), min(timestamp(finish), end)
    return max(Decimal(0), seconds(left, right))


def active_intervals(instance, actions, events, observed_at):
    """Return confirmed active intervals, stable ordering and explicit gaps."""
    gaps, transitions, seen = set(), [], set()
    by_action = {}
    for event in events:
        by_action.setdefault(event['action_id'], []).append(event)
    for action in actions:
        if action['instance_uuid'] != instance['uuid']:
            continue
        identity = action['request_id']
        if identity in seen:
            continue
        seen.add(identity)
        kind = action['action']
        if kind in UNSUPPORTED_ACTIONS:
            gaps.add('unverified_compute_transition_' + kind)
            continue
        if kind not in START_ACTIONS | STOP_ACTIONS:
            continue
        matching = [event for event in by_action.get(action['id'], [])
                    if event.get('result') == 'Success' and event.get('finish_time')
                    and str(event['event']).startswith('compute_')]
        if not matching:
            gaps.add('provider_action_completion_unavailable')
            continue
        finish = max(timestamp(event['finish_time']) for event in matching)
        transitions.append((finish, 1 if kind in START_ACTIONS else 0, identity))
    transitions.sort()
    intervals, active = [], None
    for at, running, identity in transitions:
        if at > observed_at:
            gaps.add('provider_event_in_future')
            continue
        if running:
            if active is None:
                active = at
        elif active is not None:
            if at > active:
                intervals.append((active, at))
            active = None
    launched = instance.get('launched_at')
    if not transitions and launched:
        gaps.add('provider_action_history_unavailable')
    current_active = instance.get('vm_state') in ('active', 'resized') and not instance.get('deleted')
    if active is not None:
        if current_active:
            intervals.append((active, observed_at))
        else:
            gaps.add('provider_state_history_mismatch')
    elif current_active:
        gaps.add('provider_state_history_mismatch')
    if not launched:
        gaps.add('instance_launch_unverified')
    return tuple(intervals), tuple(sorted(gaps))


def counter_delta(previous, current):
    required = ('resource_id', 'project_id', 'generation', 'observed_at', 'cpu_ns', 'network_bytes')
    if any(key not in current or key not in previous for key in required):
        raise ValueError('incomplete_counter_sample')
    if any(current[key] != previous[key] for key in ('resource_id', 'project_id', 'generation')):
        raise ValueError('counter_generation_changed')
    if timestamp(current['observed_at']) <= timestamp(previous['observed_at']):
        raise ValueError('counter_time_not_monotonic')
    result = {}
    for field in ('cpu_ns', 'network_bytes'):
        old, new = previous[field], current[field]
        if type(old) is not int or type(new) is not int or old < 0 or new < old:
            raise ValueError('counter_reset_or_unavailable')
        result[field] = new - old
    return result


def compile_usage(project_id, start, end, snapshot, counters=(), *, counters_are_ordered=False):
    observed = timestamp(snapshot['observed_at'])
    finish = min(end, timestamp(snapshot.get('source_snapshot_at', observed)))
    counter_finish = min(end, observed)
    gaps = {'object_byte_hour_history_unavailable', 'hypervisor_counters_before_enrollment_unavailable'}
    if end > observed:
        gaps.add('period_not_closed')
    samples = []
    def add(resource, metric, quantity, unit):
        samples.append(UsageSample(project_id, resource, metric, quantity, unit, start, end,
                                   SOURCE, UsageProvenance.OBSERVED))
    for instance in snapshot['instances']:
        if instance['project_id'] != project_id:
            raise ValueError('foreign_provider_instance')
        intervals, missing = active_intervals(instance, snapshot['actions'], snapshot['events'],
                                             timestamp(snapshot.get('source_snapshot_at', observed)))
        gaps.update(missing)
        if not missing:
            duration = sum((interval_seconds(a, b, start, finish) for a, b in intervals), Decimal(0))
            add(instance['uuid'], 'compute.active_vcpu_hours', duration * Decimal(instance['vcpus']) / Decimal(3600), 'vCPU-hour')
            add(instance['uuid'], 'compute.active_memory_mib_hours', duration * Decimal(instance['memory_mb']) / Decimal(3600), 'MiB-hour')
        if instance.get('launched_at'):
            until = instance.get('deleted_at') or finish
            duration = interval_seconds(instance['launched_at'], until, start, finish)
            disk = int(instance['root_gb']) + int(instance['ephemeral_gb'])
            add(instance['uuid'], 'compute.local_disk_gib_hours', duration * Decimal(disk) / Decimal(3600), 'GiB-hour')
    for key, size_key, metric in [('volumes', 'size', 'storage.volume_gib_hours'),
                                  ('snapshots', 'volume_size', 'storage.snapshot_gib_hours')]:
        for resource in snapshot[key]:
            if resource['project_id'] != project_id:
                raise ValueError('foreign_provider_storage')
            if key == 'volumes' and int(resource[size_key]) != 1:
                gaps.add('volume_size_history_unverified')
                continue
            duration = interval_seconds(resource['created_at'], resource.get('deleted_at') or finish, start, finish)
            add(resource['id'], metric, Decimal(resource[size_key]) * duration / Decimal(3600), 'GiB-hour')
    totals, previous = {}, {}
    ordered = counters if counters_are_ordered else sorted(counters, key=lambda item: timestamp(item['observed_at']))
    for counter in ordered:
        if counter['project_id'] != project_id:
            raise ValueError('foreign_counter_project')
        key = counter['resource_id']
        old = previous.get(key)
        if old:
            if old == counter:
                continue  # Identical source replay contributes no second delta or gap.
            try:
                delta = counter_delta(old, counter)
                left, right = timestamp(old['observed_at']), timestamp(counter['observed_at'])
                if seconds(left, right) > 45:
                    gaps.add('counter_sampling_gap')
                # Never prorate cumulative deltas across a billing boundary.
                if left >= start and right <= counter_finish:
                    total = totals.setdefault(key, {'cpu_ns': 0, 'network_bytes': 0})
                    for field in total:
                        total[field] += delta[field]
                elif left < start < right or left < counter_finish < right:
                    gaps.add('counter_period_boundary_unverified')
            except ValueError:
                gaps.add('counter_generation_or_sequence_gap')
        previous[key] = counter
    for resource, total in totals.items():
        add(resource, 'compute.cpu_seconds', Decimal(total['cpu_ns']) / Decimal(1000000000), 'CPU-second')
        add(resource, 'network.interface_bytes', Decimal(total['network_bytes']), 'byte')
    return UsageBatch(project_id, start, end, SOURCE, UsageProvenance.OBSERVED, False,
                      tuple(samples), observed, tuple(sorted(gaps)))


def read_meter(project_id, start, end, path='/run/openstack-poc-meter/read.sock'):
    request = json.dumps({'project_id': project_id, 'start': start.isoformat(), 'end': end.isoformat()}).encode() + b'\n'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(path)
        client.sendall(request)
        response = bytearray()
        while len(response) <= 8 * 1024 * 1024:
            block = client.recv(65536)
            if not block:
                break
            response.extend(block)
        if len(response) > 8 * 1024 * 1024:
            raise BillingError(BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)
    result = json.loads(response)
    if result.get('project_id') != project_id or result.get('error'):
        raise BillingError(BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)
    return result
