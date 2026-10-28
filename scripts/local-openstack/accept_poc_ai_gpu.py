#!/usr/bin/python3
"""Bounded GPU neural classification on synthetic data; CUDA is mandatory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import threading
import time

os.environ['CUBLAS_WORKSPACE_CONFIG'] = ':4096:8'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import torch
    torch.set_num_threads(2)
    torch.manual_seed(1729)
    torch.use_deterministic_algorithms(True)
    torch.backends.cuda.matmul.allow_tf32 = False
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA_required_no_CPU_fallback')
    torch.cuda.set_device(0)
    torch.cuda.reset_peak_memory_stats()
    stop, readings = threading.Event(), []

    def monitor():
        while not stop.is_set():
            result = subprocess.run(['/usr/lib/wsl/lib/nvidia-smi',
                '--query-gpu=utilization.gpu,memory.used', '--format=csv,noheader,nounits'],
                text=True, capture_output=True, timeout=5)
            if result.returncode == 0:
                try:
                    utilization, memory = [int(value.strip()) for value in result.stdout.strip().split(',')]
                    readings.append({'gpu_utilization_percent': utilization, 'device_memory_used_mib': memory})
                except ValueError:
                    pass
            stop.wait(.2)

    sampler = threading.Thread(target=monitor, daemon=True)
    sampler.start()
    begin = time.monotonic()
    try:
        # Four synthetic Gaussian classes with known labels; seed and generation
        # are public and contain no customer data or downloaded model weights.
        labels = torch.arange(6144) % 4
        inputs = torch.randn(6144, 64) * .25
        inputs[torch.arange(6144), labels] += 3
        model = torch.nn.Sequential(torch.nn.Linear(64, 256), torch.nn.ReLU(), torch.nn.Linear(256, 4)).cuda()
        x, y = inputs[:4096].cuda(), labels[:4096].cuda()
        optimizer = torch.optim.Adam(model.parameters(), lr=.005)
        model.train()
        for step in range(120):
            indices = torch.arange(step * 256, step * 256 + 256, device='cuda') % len(x)
            optimizer.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(model(x[indices]), y[indices])
            loss.backward()
            optimizer.step()
        torch.cuda.synchronize()
        training_seconds = time.monotonic() - begin
        model.eval()
        test = inputs[4096:].cuda()
        with torch.inference_mode():
            outputs = model(test)
            accuracy = float((outputs.argmax(1).cpu() == labels[4096:]).float().mean())
            gpu_reference = outputs[:128].cpu()
            weights = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
            reference = torch.nn.Sequential(torch.nn.Linear(64, 256), torch.nn.ReLU(), torch.nn.Linear(256, 4))
            reference.load_state_dict(weights)
            cpu_reference = reference(inputs[4096:4224])
            maximum_error = float((cpu_reference - gpu_reference).abs().max())
            timings = []
            for ignored in range(100):
                started = time.perf_counter()
                model(test)
                torch.cuda.synchronize()
                timings.append((time.perf_counter() - started) * 1000)
            # Keep the small inference workload active long enough for NVML's
            # utilization sampling window; this is not a host stress test.
            until = time.monotonic() + 6
            sustained_batches = 0
            while time.monotonic() < until:
                model(test)
                torch.cuda.synchronize()
                sustained_batches += 1
        elapsed = time.monotonic() - begin
        digest = lambda data: hashlib.sha256(data).hexdigest()
        report = {'schema': 1, 'environment': 'existing Ubuntu WSL CUDA; not an OpenStack tenant VM',
            'workload': 'seeded four-class Gaussian MLP training and held-out inference', 'seed': 1729,
            'torch_version': torch.__version__, 'cuda_runtime': torch.version.cuda,
            'gpu': torch.cuda.get_device_name(0), 'training_examples': 4096, 'held_out_examples': 2048,
            'training_steps': 120, 'training_seconds': training_seconds, 'held_out_accuracy': accuracy,
            'cpu_reference_max_absolute_error': maximum_error,
            'latency_batch_2048_p50_ms': statistics.median(timings),
            'latency_batch_2048_p95_ms': sorted(timings)[94],
            'inference_examples_per_second': 2048 * 100 / (sum(timings) / 1000),
            'telemetry_sustained_batches': sustained_batches, 'total_seconds': elapsed,
            'peak_cuda_allocated_bytes': torch.cuda.max_memory_allocated(),
            'peak_cuda_reserved_bytes': torch.cuda.max_memory_reserved(),
            'process_peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'telemetry_samples': len(readings),
            'peak_gpu_utilization_percent': max((r['gpu_utilization_percent'] for r in readings), default=0),
            'peak_device_memory_used_mib': max((r['device_memory_used_mib'] for r in readings), default=0),
            'input_sha256': digest(inputs.numpy().tobytes()),
            'model_sha256': digest(b''.join(value.numpy().tobytes() for value in weights.values())),
            'held_out_output_sha256': digest(outputs.cpu().numpy().tobytes()),
            'cpu_fallback': False, 'tenant_gpu_access_verified': False, 'mig_verified': False}
        report['passed'] = (accuracy >= .95 and maximum_error <= 1e-4 and elapsed < 120
            and report['peak_cuda_reserved_bytes'] < 2 * 1024**3 and report['peak_gpu_utilization_percent'] > 0)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        args.output.chmod(0o644)
        print(json.dumps(report), flush=True)
        if not report['passed']:
            raise RuntimeError('GPU_acceptance_threshold_failed')
    finally:
        stop.set()
        sampler.join(timeout=6)


if __name__ == '__main__':
    main()
