#!/usr/bin/python3
"""Collect only enrolled projects using the separate reader/collector identity.

Run as the Horizon service account. Stores real provider observations and
explicit coverage gaps; never creates resources or fabricates missing meters.
"""
import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys

from openstack_cloud_poc.poc_metering import collector_config, collect_project, month_bounds
from openstack_cloud_poc.usage_history import UsageHistory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('/var/lib/openstack-poc-billing'))
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    config = collector_config(args.state_dir / 'collector.json')
    history = UsageHistory(args.state_dir / 'usage.sqlite')
    start, end = month_bounds(datetime.now(timezone.utc))
    results = []
    failed = False
    for project in config['project_ids']:
        try:
            batch, inventory = collect_project(config, project, start, end)
            identifier = history.append(batch, inventory)
            results.append({'project_id': project, 'snapshot_id': identifier,
                             'sample_count': len(batch.samples), 'inventory_gaps': inventory['gaps'],
                             'complete': batch.complete})
        except Exception as error:
            failed = True
            results.append({'project_id': project, 'error_type': type(error).__name__})
    print(json.dumps({'status': 'partial' if failed else 'collected', 'projects': results}, sort_keys=True))
    return 2 if failed else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status': 'unavailable', 'error_type': type(error).__name__}))
        raise SystemExit(2)
