"""Verify a closed prospective source-snapshot billing epoch.

CPU/network charge exact counter deltas between recorded source endpoints;
their acquisition offsets are retained and bounded, never prorated. Swift
charges byte residence at successful proxy-response completion. Certification
requires a complete initial object inventory, durable request/outcome history,
reconciled terminal inventory and continuous stable-generation counters.
"""
from dataclasses import replace
from decimal import Decimal
import hashlib
import json

from .provider_usage import compile_usage, counter_delta, seconds, timestamp, SOURCE
from .usage import UsageSample, UsageProvenance


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def compile_closed_period(project, snapshots, initial_objects, terminal_objects, events, requests,
                          *, poisoned=False, maximum_gap_seconds=45, maximum_offset_seconds=2):
    if len(snapshots) < 2:
        raise ValueError('two_source_boundaries_required')
    ordered = sorted(snapshots, key=lambda row: timestamp(row['observed_at']))
    start, end = timestamp(ordered[0]['observed_at']), timestamp(ordered[-1]['observed_at'])
    if not start < end:
        raise ValueError('closed_period_boundaries_invalid')
    if any(row['project_id'] != project for row in ordered):
        raise ValueError('foreign_period_snapshot')
    base = compile_usage(project, start, end, ordered[-1])
    gaps = set(base.incomplete_reasons) - {'object_byte_hour_history_unavailable',
                                         'hypervisor_counters_before_enrollment_unavailable'}
    if poisoned:
        gaps.add('swift_journal_persistence_failure')
    if not all(row.get('source_clock_checked') is True for row in ordered):
        gaps.add('period_source_clock_unverified')
    for previous, current in zip(ordered, ordered[1:]):
        if seconds(timestamp(previous['observed_at']), timestamp(current['observed_at'])) > maximum_gap_seconds:
            gaps.add('period_source_sampling_gap')
    active_ids = {row.resource_id for row in base.samples
                  if row.metric == 'compute.active_vcpu_hours' and row.quantity > 0}
    samples, endpoints = list(base.samples), {}
    for resource in sorted(active_ids):
        readings = []
        missing = False
        for snapshot in ordered:
            candidates = [counter for counter in snapshot.get('counters', []) if counter['resource_id'] == resource]
            if len(candidates) != 1:
                missing = True
                continue
            counter = candidates[0]
            if counter['project_id'] != project:
                raise ValueError('foreign_period_counter')
            if abs(seconds(timestamp(counter['observed_at']), timestamp(snapshot['observed_at']))) > maximum_offset_seconds:
                gaps.add('counter_acquisition_offset_exceeded')
            readings.append(counter)
        if missing or len(readings) != len(ordered):
            gaps.add('resource_counter_start_end_or_deletion_gap')
            continue
        total = {'cpu_ns': 0, 'network_bytes': 0}
        for previous, current in zip(readings, readings[1:]):
            try:
                if previous == current:
                    continue
                delta = counter_delta(previous, current)
                for field in total:
                    total[field] += delta[field]
            except ValueError:
                gaps.add('counter_reset_generation_or_conflict')
        endpoints[resource] = {'start': readings[0]['observed_at'], 'end': readings[-1]['observed_at'],
                               'generation': readings[0]['generation']}
        for metric, quantity, unit in (
            ('compute.cpu_seconds', Decimal(total['cpu_ns']) / 1000000000, 'CPU-second'),
            ('network.interface_bytes', Decimal(total['network_bytes']), 'byte')):
            samples.append(UsageSample(project, resource, metric, quantity, unit, start, end, SOURCE, UsageProvenance.OBSERVED))

    objects = dict(initial_objects)
    if any(type(value) is not int or value < 0 for value in objects.values()):
        raise ValueError('invalid_initial_object_inventory')
    for request in requests:
        if request['project_id'] != project:
            raise ValueError('foreign_swift_request')
        request_at = timestamp(request['at'])
        if (start <= request_at < end and (request.get('known') != 1 or request.get('status') is None)
                or request_at < start and request.get('status') is None):
            gaps.add('swift_pending_or_unknown_request')
    last, byte_seconds, seen = start, Decimal(0), {}
    for event in sorted(events, key=lambda row: (timestamp(row['at']), row['sequence'])):
        if event['project_id'] != project:
            raise ValueError('foreign_swift_event')
        at = timestamp(event['at'])
        if not start <= at < end:
            continue
        identity = event['event_id']
        if identity in seen:
            if seen[identity] != event:
                gaps.add('swift_conflicting_duplicate_event')
            continue
        seen[identity] = event
        byte_seconds += sum(objects.values()) * seconds(last, at)
        last = at
        if event.get('known') != 1:
            gaps.add('swift_unknown_mutation')
            continue
        if event['method'] == 'PUT' and type(event.get('size_bytes')) is int and event['size_bytes'] >= 0:
            objects[event['object_key']] = event['size_bytes']
        elif event['method'] == 'DELETE':
            objects.pop(event['object_key'], None)
        elif event['method'] != 'POST':
            gaps.add('swift_unsupported_mutation')
    byte_seconds += sum(objects.values()) * seconds(last, end)
    if objects != terminal_objects:
        gaps.add('swift_terminal_inventory_mismatch')
    samples.append(UsageSample(project, 'swift-account-' + project, 'storage.object_byte_hours',
        byte_seconds / 3600, 'byte-hour', start, end, SOURCE, UsageProvenance.OBSERVED))
    batch = replace(base, samples=tuple(samples), complete=not gaps, incomplete_reasons=tuple(sorted(gaps)))
    evidence = {'policy': 'source_snapshot_counter_deltas_and_proxy_completion_byte_hours_v1',
        'counter_endpoints': endpoints, 'maximum_acquisition_offset_seconds': maximum_offset_seconds,
        'maximum_source_gap_seconds': maximum_gap_seconds, 'snapshot_count': len(ordered),
        'snapshot_hashes': [fingerprint(row) for row in ordered], 'initial_objects_hash': fingerprint(initial_objects),
        'terminal_objects_hash': fingerprint(terminal_objects), 'swift_events_hash': fingerprint(events),
        'swift_requests_hash': fingerprint(requests), 'swift_byte_seconds': str(byte_seconds),
        'historical_backfill': False}
    return batch, evidence
