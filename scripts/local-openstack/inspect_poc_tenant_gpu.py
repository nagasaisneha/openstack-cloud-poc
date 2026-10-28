#!/usr/bin/python3
"""Verify the existing marked Nova guest's actual accelerator device path."""
import json
import logging
from pathlib import Path
import xml.etree.ElementTree as ET

import continue_poc as task
import manage_cinder_loop as safety
import verify_poc_guest_access as guest


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    import libvirt
    source, private = task.initialize()
    ingress = task.read('connectivity', source)
    identity = json.loads(guest.ssh(source, ingress, 'a',
        'wget -q -T 5 -O - http://169.254.169.254/openstack/latest/meta_data.json\n', pin_only=True))
    safety.require(identity['uuid'] == source['resources']['a']['server']['id'], 'guest_gpu_probe_identity_mismatch')
    devices = guest.ssh(source, ingress, 'a', '''set -eu
count=0
for vendor in /sys/bus/pci/devices/*/vendor; do
    [ -f "$vendor" ] || continue
    if [ "$(cat "$vendor")" = 0x10de ]; then count=$((count+1)); fi
done
printf 'nvidia_pci_count=%s\\n' "$count"
if [ -c /dev/dxg ]; then printf 'dxg=yes\\n'; else printf 'dxg=no\\n'; fi
if [ -c /dev/nvidia0 ]; then printf 'nvidia0=yes\\n'; else printf 'nvidia0=no\\n'; fi
''', pin_only=True)
    with libvirt.openReadOnly('qemu:///system') as connection:
        domain = connection.lookupByUUIDString(identity['uuid'])
        tree = ET.fromstring(domain.XMLDesc(0))
        hostdev_count = len(tree.findall('./devices/hostdev'))
    report = {'environment': 'existing tenant A Nova guest', 'pinned_ssh_uuid_verified': True,
        'guest_device_readback': devices.strip().splitlines(), 'libvirt_hostdev_count': hostdev_count,
        'tenant_gpu_access_verified': False,
        'blocker': 'WSL exposes CUDA through dxg; no assignable NVIDIA PCI device or tenant hostdev exists',
        'needed': 'approved bare-metal KVM compute with NVIDIA PCI GPU, IOMMU group/reset support and Nova PCI scheduling'}
    path = Path(__file__).resolve().parents[2] / 'docs/evidence/tenant-gpu-path-20261028.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    path.chmod(0o644)  # Sanitized acceptance summary; contains no credentials.
    print(json.dumps(report), flush=True)
