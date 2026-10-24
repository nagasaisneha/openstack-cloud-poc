"""ARP frame and bounded socket tests; no packets are sent."""
import ipaddress
import socket
import struct
import unittest
from unittest.mock import MagicMock, patch

from openstack_cloud_poc import packet_probe as setup

SRC = 'fa:16:3e:11:22:33'
DST = 'fa:16:3e:44:55:66'
SOURCE_IP, TARGET_IP = '172.30.241.2', '172.30.241.100'


def reply():
    return (setup.mac_bytes(SRC) + setup.mac_bytes(DST) + b'\x08\x06'
        + struct.pack('!HHBBH', 1, 0x0800, 6, 4, 2)
        + setup.mac_bytes(DST) + ipaddress.IPv4Address(TARGET_IP).packed
        + setup.mac_bytes(SRC) + ipaddress.IPv4Address(SOURCE_IP).packed)


class PacketProbeTests(unittest.TestCase):
    def test_request_is_broadcast_arp_with_real_source_and_only_target_ipv4(self):
        frame = setup.request_frame(SRC, SOURCE_IP, TARGET_IP)
        self.assertEqual(len(frame), 60)
        self.assertEqual(frame[:6], b'\xff' * 6)
        self.assertEqual(frame[6:12], setup.mac_bytes(SRC))
        self.assertEqual(frame[14:22], struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1))
        self.assertEqual(frame[28:32], ipaddress.IPv4Address(SOURCE_IP).packed)
        self.assertEqual(frame[38:42], ipaddress.IPv4Address(TARGET_IP).packed)

    def test_reply_requires_matching_protocol_operation_addresses_and_ethernet_sender(self):
        self.assertEqual(setup.matching_reply(reply(), SRC, SOURCE_IP, TARGET_IP), DST)
        for offset in (0, 6, 12, 14, 16, 18, 20, 22, 28, 32, 38):
            altered = bytearray(reply())
            altered[offset] ^= 1
            self.assertIsNone(setup.matching_reply(altered, SRC, SOURCE_IP, TARGET_IP))
        self.assertIsNone(setup.matching_reply(reply()[:40], SRC, SOURCE_IP, TARGET_IP))
        self.assertIsNone(setup.matching_reply(setup.request_frame(SRC, SOURCE_IP, TARGET_IP), SRC, SOURCE_IP, TARGET_IP))

    def test_invalid_mac_and_non_unicast_probe_endpoints_are_rejected(self):
        for mac in ('invalid', 'ff:ff:ff:ff:ff:ff', '00:00:00:00:00:00'):
            with self.assertRaises(ValueError):
                setup.mac_bytes(mac)
        for target in ('0.0.0.0', '127.0.0.1', '224.0.0.1', SOURCE_IP):
            with self.assertRaises(ValueError):
                setup.request_frame(SRC, SOURCE_IP, target)

    def socket(self):
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.getsockname.return_value = ('tap-test', 0x0806, 0, 1, setup.mac_bytes(SRC))
        stream.send.return_value = 60
        return stream

    def test_three_observed_reply_windows_are_recorded_without_real_network(self):
        stream = self.socket()
        stream.recv.side_effect = [reply(), socket.timeout(), reply(), socket.timeout(), reply(), socket.timeout()]
        with patch.object(setup.os, 'geteuid', return_value=0), patch.object(setup.socket, 'socket', return_value=stream):
            result = setup.probe('tap-test', SOURCE_IP, TARGET_IP)
        self.assertEqual(result['replies'], [[DST], [DST], [DST]])
        self.assertEqual(stream.send.call_count, 3)

    def test_empty_observation_is_distinct_from_socket_or_send_failure(self):
        stream = self.socket()
        stream.recv.side_effect = socket.timeout()
        with patch.object(setup.os, 'geteuid', return_value=0), patch.object(setup.socket, 'socket', return_value=stream):
            self.assertEqual(setup.probe('tap-test', SOURCE_IP, TARGET_IP)['replies'], [[], [], []])
            stream.send.return_value = 0
            with self.assertRaisesRegex(RuntimeError, 'completely sent'):
                setup.probe('tap-test', SOURCE_IP, TARGET_IP)


if __name__ == '__main__':
    unittest.main()
