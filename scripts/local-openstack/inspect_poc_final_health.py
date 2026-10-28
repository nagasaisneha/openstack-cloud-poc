#!/usr/bin/python3
"""Read-only post-acceptance health, guest identity and billing preservation checks."""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3

import browser_resources as controls
import configure_nova as common
import continue_poc as task
import manage_cinder_loop as safety
import verify_poc_guest_access as guest


def main():
    logging.disable(logging.CRITICAL)
    source, private = task.initialize()
    root = Path(__file__).resolve().parents[2]
    with task.locked():
        status = json.loads(Path('/var/lib/openstack-poc-operations/status.json').read_text())
        now = datetime.now(timezone.utc)
        age = (now - datetime.fromisoformat(status['generated_at'])).total_seconds()
        safety.require(0 <= age <= 45, 'operations_snapshot_stale')
        safety.require(len(status['probes']) == 45 and status['active_alerts'] == 0
                       and all(row['healthy'] for row in status['probes'].values()),
                       'operations_probe_unhealthy')

        cinder = safety.read_state()
        device = safety.owned_device(safety.inventory())
        safety.require(device == cinder['loop_device'] and device is not None,
                       'cinder_image_attachment_changed')
        pv = safety.pv_identity(device)
        safety.require(pv['pv_uuid'] == cinder['pv_uuid'] and pv['vg_name'] == safety.VG,
                       'original_cinder_pv_identity_changed')

        ingress = task.read('connectivity', source)
        for label in ('a', 'b'):
            with controls.connection(source, private, label) as cloud:
                vm, network, port = task.validate_tenant(cloud, source, label)
                safety.require(vm.status == 'ACTIVE', 'tenant_guest_inactive')
            metadata = json.loads(guest.ssh(source, ingress, label,
                'set -eu\nwget -q -T 5 -O - http://169.254.169.254/openstack/latest/meta_data.json\n',
                pin_only=True))
            safety.require(metadata['uuid'] == vm.id, 'pinned_ssh_guest_identity_changed')

        lb_state = task.read('octavia-acceptance', source)
        with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
            retained = operator.compute.get_server(source['retained_vm_id'])
            safety.require(retained.status == 'ACTIVE' and retained.project_id == source['retained_project_id'],
                           'retained_vm_not_active')
            lb = operator.load_balancer.get_load_balancer(lb_state['resources']['loadbalancer']['id'])
            safety.require(lb.project_id == source['projects']['a']['id']
                           and lb.provisioning_status == 'ACTIVE' and lb.operating_status == 'ONLINE',
                           'retained_demo_load_balancer_unhealthy')

        journal = Path('/var/lib/openstack-poc-swift-meter/events.sqlite')
        safety.require(not journal.with_suffix('.failed').exists(), 'swift_journal_poison_marker')
        with sqlite3.connect('file:' + str(journal) + '?mode=ro', uri=True) as db:
            pending = db.execute('SELECT count(*) FROM requests r LEFT JOIN completions c '
                                 'USING(request_id) WHERE c.request_id IS NULL').fetchone()[0]
        safety.require(pending == 0, 'swift_request_outcome_pending')
        evidence = json.loads((root / 'docs/evidence/closed-billing-period-20261028.json').read_text())
        with sqlite3.connect('file:/var/lib/openstack-poc-meter/evidence.sqlite?mode=ro', uri=True) as db:
            row = db.execute('SELECT payload FROM sealed_periods WHERE project_id=? AND start=? AND end=?',
                (source['projects']['a']['id'], evidence['start'], evidence['end'])).fetchone()
        safety.require(row is not None and json.loads(row[0])['measurement_policy']['sealed_period']
                       == evidence['sealed_period'], 'verified_closed_period_not_preserved')

        result = {'schema': 1, 'observed_at': now.isoformat(),
            'operations': {'probe_count': 45, 'unhealthy': 0, 'active_alerts': 0,
                           'snapshot_age_seconds': round(age, 3)},
            'checks': {'original_cinder_pv_and_image': True, 'retained_vm_active': True,
                'tenant_a_b_active_pinned_ssh_uuid': True, 'guest_and_vip_http_probes_healthy': True,
                'original_demo_lb_active_online': True, 'swift_journal_no_poison_or_pending': True,
                'certified_closed_period_preserved': True},
            'limits': {'staging_deployed': False, 'tenant_gpu_verified': False,
                       'historical_billing_complete': False}}
        output = root / 'docs/evidence/final-local-health-20261029.json'
        output.write_text(json.dumps(result, indent=2) + '\n')
        output.chmod(0o644)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'final_health_check_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
