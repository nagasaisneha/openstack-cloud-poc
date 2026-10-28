#!/usr/bin/python3
"""Read existing Windows/WSL capacity; never provisions or reconfigures a host."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def main():
    script = r'''$ErrorActionPreference='Stop'
[ordered]@{
computer=(Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory,NumberOfLogicalProcessors)
processors=@(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors)
operating_system=(Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,FreePhysicalMemory,TotalVisibleMemorySize)
physical_disks=@(Get-PhysicalDisk | Select-Object FriendlyName,Size,BusType,MediaType,CanPool,HealthStatus)
logical_disks=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,Size,FreeSpace)
physical_adapters=@(Get-NetAdapter -Physical | Select-Object InterfaceDescription,Status,LinkSpeed)
} | ConvertTo-Json -Depth 6
'''
    result = subprocess.run(['/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe',
        '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('read_only_windows_capacity_probe_failed')
    windows = json.loads(result.stdout.lstrip('\ufeff'))
    memory = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
            memory[key + '_kib'] = int(value.split()[0])
    try:
        import libvirt
        with libvirt.openReadOnly('qemu:///system') as connection:
            active_guests = sum(bool(domain.isActive()) for domain in connection.listAllDomains())
    except Exception:
        active_guests = None
    report = {'schema': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
        'selected_direction': 'existing local hardware; no rental or purchase authorized',
        'budget_approver': 'Tribhuvan', 'release_platform_network_storage_security_operations_billing_owner': 'Sneha',
        'windows': windows, 'existing_wsl_memory': memory, 'dev_kvm_present': Path('/dev/kvm').exists(),
        'actual_active_libvirt_guests': active_guests,
        'nvidia_pci_devices': [path.name for path in Path('/sys/bus/pci/devices').glob('*')
            if (path / 'vendor').exists() and (path / 'vendor').read_text().strip() == '0x10de'],
        'constraints': [
            '16_GiB_physical_host_below_32_GiB_full_scope_single_host_planning_guard',
            'existing_POC_must_retain_its_memory_guests_and_data',
            'one_inspected_machine_does_not_provide_multi_host_failure_domains',
            'no_approved_empty_Ceph_or_Swift_devices_or_provider_fabric_allocations',
            'WSL_dxg_is_not_an_assignable_NVIDIA_PCI_device_for_Nova'],
        'additional_owned_local_host_inventory': 'none; user confirmed only current ACER', 'only_current_acer_available': True, 'staging_deployed': False, 'staging_ready': False,
        'changes_performed': []}
    output = Path(__file__).resolve().parents[2] / 'docs/evidence/local-staging-hardware-20261029.json'
    output.write_text(json.dumps(report, indent=2) + '\n')
    output.chmod(0o644)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
