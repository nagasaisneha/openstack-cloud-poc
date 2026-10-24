"""Bounded ARP observations used only by the operator-run local acceptance.

The parent validates task ownership and namespace/interface/IP scope. This
child sends three requests without changing addresses, routes or policy.
"""
import ipaddress
import json
import os
import re
import socket
import struct
import sys
import time


def mac_bytes(mac):
    if not re.fullmatch(r'[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}', mac):
        raise ValueError('invalid Ethernet address')
    value = bytes.fromhex(mac.replace(':', ''))
    if value == b'\0' * 6 or value[0] & 1:
        raise ValueError('expected a unicast Ethernet address')
    return value


def request_frame(source_mac, source_ip, target_ip):
    source = mac_bytes(source_mac)
    src, dst = ipaddress.IPv4Address(source_ip), ipaddress.IPv4Address(target_ip)
    if any(ip.is_unspecified or ip.is_multicast or ip.is_loopback for ip in (src, dst)) or src == dst:
        raise ValueError('invalid ARP probe endpoints')
    ethernet = b'\xff' * 6 + source + b'\x08\x06'
    arp = struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1) + source + src.packed + b'\0' * 6 + dst.packed
    return (ethernet + arp).ljust(60, b'\0')


def matching_reply(frame, source_mac, source_ip, target_ip):
    source = mac_bytes(source_mac)
    if len(frame) < 42 or frame[:6] != source or frame[12:14] != b'\x08\x06':
        return None
    if frame[14:22] != struct.pack('!HHBBH', 1, 0x0800, 6, 4, 2):
        return None
    if (frame[22:28] != frame[6:12] or frame[32:38] != source
            or frame[28:32] != ipaddress.IPv4Address(target_ip).packed
            or frame[38:42] != ipaddress.IPv4Address(source_ip).packed):
        return None
    sender = ':'.join(f'{octet:02x}' for octet in frame[22:28])
    try:
        mac_bytes(sender)
    except ValueError:
        return None
    return sender


def probe(interface, source_ip, target_ip):
    if os.geteuid() != 0 or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', interface):
        raise ValueError('root and an explicit network interface are required')
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806)) as stream:
        stream.bind((interface, 0))
        address = stream.getsockname()[4]
        if len(address) != 6:
            raise ValueError('probe requires an Ethernet interface')
        source_mac = ':'.join(f'{octet:02x}' for octet in address)
        request = request_frame(source_mac, source_ip, target_ip)
        observations = []
        for _ in range(3):
            if stream.send(request) != len(request):
                raise RuntimeError('ARP request was not completely sent')
            deadline = time.monotonic() + 1.0
            replies = set()
            for _ in range(256):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                stream.settimeout(remaining)
                try:
                    frame = stream.recv(2048)
                except socket.timeout:
                    break
                sender = matching_reply(frame, source_mac, source_ip, target_ip)
                if sender:
                    replies.add(sender)
            else:
                raise RuntimeError('ARP receive limit exceeded; probe is unverified')
            observations.append(sorted(replies))
    return {'attempts': 3, 'source_mac': source_mac, 'source_ip': source_ip,
            'target_ip': target_ip, 'replies': observations}


def main():
    try:
        if len(sys.argv) != 4:
            raise ValueError('expected interface, source IPv4 and target IPv4')
        print(json.dumps(probe(*sys.argv[1:]), sort_keys=True))
        return 0
    except Exception:
        print('ARP probe failed; no isolation result established', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
