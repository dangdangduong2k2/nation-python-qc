"""Wire/parser regression tests. Synthetic data is not hardware evidence."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parent/'src'))
from test_qc import Fake
from qc_uart import QCUART
from zk_nation.core.protocol import Response,crc16
from zk_nation.core.reader import parse_inventory
from zk_nation.core.errors import ProtocolError
from zk_nation.vendor.nrn import NRNReader
from zk_nation.adapter import checked_frame


class ExtendedFake(Fake):
    support_fast=True
    support_phase=True
    def write(self,wire):
        if wire[2]!=1: return super().write(wire)
        self.writes.append(wire)
        epc=bytes(self.banks[1][4:16]);tid=bytes(self.banks[2])
        has_tid=bool(wire[3]&32) and self.support_fast
        has_phase=bool(wire[3]&16) and self.support_phase
        data=epc+(tid if has_tid else b'')
        flags=len(data)|(128 if has_tid else 0)|(64 if has_phase else 0)
        data=bytes([1,1,flags])+data+bytes([65])
        if has_phase: data+=bytes.fromhex('010002000E0A3D') # 920125 kHz
        body=bytes([len(data)+5,0,1,1])+data
        self.buffer.extend(body+crc16(body).to_bytes(2,'little'))
        return len(wire)


class PhaseFastIDTests(unittest.TestCase):
    def make(self,**kwargs):
        fake=ExtendedFake();fake.query=bytes([4,0]);u=QCUART('test',serial_factory=lambda **kw:fake,**kwargs)
        u.open();self.addCleanup(u.close)
        return fake,u
    def scan(self,u,extra=b'\x02\0\x06'):
        u.send(NRNReader.build_frame(0x210,b'\0\0\0\1\0'+extra))
        self.assertEqual(checked_frame(u.receive(1024))['data'],b'\0')
        tag=checked_frame(u.receive(1024));end=checked_frame(u.receive(1024))
        self.assertEqual((end['category'],end['mid'],end['data']),(18,1,b'\0'))
        self.assertIsNone(u.last_inventory_error)
        return tag['data']
    def test_combined_record_and_native_nation_parser(self):
        fake,u=self.make();payload=self.scan(u)
        parsed=NRNReader('simulation',115200).parse_epc(payload)
        self.assertEqual(parsed['epc'],bytes(fake.banks[1][4:16]).hex().upper())
        self.assertEqual(parsed['tid'],bytes(fake.banks[2]).hex().upper())
        self.assertAlmostEqual(parsed['frequency'],920.125)
        self.assertAlmostEqual(parsed['phase'],16/128*2*3.14159265359)
        self.assertEqual(u.inventory_stats,dict(reports=1,phase_reports=1,fast_tid_reports=1,tid_read_commands=0))
        self.assertEqual([w[3] for w in fake.writes if w[2]==1],[0x34])
        self.assertEqual(len([w for w in fake.writes if w[2]==2]),1) # PC only
    def test_non_fastid_tag_falls_back_to_selected_tid_read(self):
        fake,u=self.make();fake.support_fast=False
        data=self.scan(u);self.assertIn(bytes(fake.banks[2]),data)
        self.assertEqual(u.inventory_stats['tid_read_commands'],1)
        self.assertEqual(u.inventory_stats['fast_tid_reports'],0)
    def test_flags_off_preserves_old_inventory_path(self):
        fake,u=self.make(phase=False,fast_tid=False);self.scan(u)
        self.assertEqual([w[3] for w in fake.writes if w[2]==1],[4])
        self.assertEqual(u.inventory_stats['phase_reports'],0)
        self.assertEqual(u.inventory_stats['tid_read_commands'],1)
    def test_no_phase_extension_does_not_fabricate_phase(self):
        fake,u=self.make();fake.support_phase=False
        data=self.scan(u);self.assertNotIn('phase',NRNReader('simulation',115200).parse_epc(data))
        self.assertEqual(u.inventory_stats['phase_reports'],0)
    def test_epc_only_does_not_enable_fastid(self):
        fake,u=self.make();self.scan(u,b'')
        self.assertEqual([w[3] for w in fake.writes if w[2]==1],[0x14])
    def test_short_tid_uses_fastid_prefix(self):
        fake,u=self.make();data=self.scan(u,b'\x02\1\3')
        self.assertEqual(NRNReader('simulation',115200).parse_epc(data)['tid'],bytes(fake.banks[2][:6]).hex().upper())
        self.assertEqual(u.inventory_stats['tid_read_commands'],0)
    def test_parser_rejects_truncated_extensions_and_invalid_fastid(self):
        data=bytes.fromhex('0101D8')+bytes(24)+b'\x41'+bytes.fromhex('010002000E0A3D')
        for length in range(len(data)):
            with self.subTest(length=length),self.assertRaises(ProtocolError):
                parse_inventory(Response(0,1,1,data[:length]),1)
        tag=parse_inventory(Response(0,1,1,data),1)[0]
        self.assertEqual(tag['phase_end_raw'],512)
        self.assertEqual(tag['frequency_khz'],920125)
        with self.assertRaises(ProtocolError): parse_inventory(Response(0,1,1,b'\1\1\x8c'+bytes(13)),1)
    def test_cfg9_phase_get_and_partial_set_preserve_flags(self):
        fake,u=self.make();fake.query=bytes([0x14,0])
        self.assertEqual(u.zk.query()['q'],4)
        self.assertTrue(u.zk.query()['phase'])
        self.assertEqual(u.zk.query(q=5)['q_flags_raw'],0x15)
        self.assertEqual(u.zk.query(session=1)['q_flags_raw'],0x15)
        self.assertEqual(u.zk.query(phase=False)['q_flags_raw'],5)
        self.assertEqual(u.zk.query(fast_tid=True)['q_flags_raw'],0x25)


if __name__=='__main__': unittest.main()
