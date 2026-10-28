#!/usr/bin/python3
"""Read-only accelerator inventory; never infers tenant GPU access from WSL."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess


def probe(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return {'exit': result.returncode, 'stdout': result.stdout[:6000], 'stderr': result.stderr[:500]}
    except Exception as error:
        return {'error': type(error).__name__}


if __name__ == '__main__':
    binary = shutil.which('nvidia-smi') or '/usr/lib/wsl/lib/nvidia-smi'
    print(json.dumps({'environment': 'existing Ubuntu WSL, not a tenant VM',
        'dxg_present': Path('/dev/dxg').exists(),
        'nvidia_devices': [str(path) for path in Path('/dev').glob('nvidia*')],
        'cuda_driver_library': Path('/usr/lib/wsl/lib/libcuda.so.1').exists(),
        'nvidia_smi': probe([binary, '--query-gpu=name,uuid,memory.total,driver_version,utilization.gpu,memory.used', '--format=csv,noheader']),
        'python_runtimes': {name: importlib.util.find_spec(name) is not None for name in ('torch', 'onnxruntime', 'cupy', 'numpy')},
        'docker_images': probe(['docker', 'image', 'ls', '--format', '{{.Repository}}:{{.Tag}} {{.ID}}']),
        'nvidia_pci_devices': [path.name for path in Path('/sys/bus/pci/devices').glob('*')
            if (path / 'vendor').exists() and (path / 'vendor').read_text().strip() == '0x10de'],
    }, indent=2))
