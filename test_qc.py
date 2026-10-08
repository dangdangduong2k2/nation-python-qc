import sys
from pathlib import Path
import time
import unittest
sys.path.insert(0,str(Path(__file__).parent/'src'))
from qc_uart import QCUART
from service_bridge import FrameAssembler
from zk_nation.core.simulation import MemorySerial
from zk_nation.vendor.nrn import NRNReader
from zk_nation.adapter import checked_frame


class Fake(MemorySerial):
    def __init__(self):
        super().__init__(1);self.profile_id=141;self.region=bytes([1,17,2]);self.writes=[]
    def reset_input_buffer(self): self.buffer.clear()
    def write(self,raw): self.writes.append(raw);return super().write(raw)


class QCTests(unittest.TestCase):
    def setUp(self):
        self.fake=Fake();self.u=QCUART('test',serial_factory=lambda **kw:self.fake,allow_approximate=True)
        self.u.open();self.addCleanup(self.u.close)
    def exchange(self,mid,data=b''):
        self.u.send(NRNReader.build_frame(mid,data));return checked_frame(self.u.receive(65536))
    def test_info_ability_and_power(self):
        self.assertEqual(self.exchange(0x100)['data'],b'\0\0')
        self.assertEqual(self.exchange(0x200)['data'],bytes([0,30,1,0,5,0,1,3,4,5,0,1,0]))
        self.assertEqual(self.exchange(0x201,bytes([1,18,255,0]))['data'],b'\0')
        self.assertEqual(self.exchange(0x202)['data'],bytes([1,18]))
    def test_rf_band_and_frequency(self):
        for band in (0,1,3,4,5):
            with self.subTest(band=band):
                self.assertEqual(self.exchange(0x203,bytes([band,1,0]))['data'],b'\0')
                self.assertEqual(self.exchange(0x204)['data'],bytes([band]))
        self.exchange(0x203,b'\0\1\0')
        self.assertEqual(self.exchange(0x205,bytes([0,1,0,3,1,2,3,2,0]))['data'],b'\0')
        self.assertEqual(self.exchange(0x206)['data'],bytes([0,0,3,1,2,3]))
        e=self.exchange(0x205,bytes([0,1,0,2,1,3]));self.assertEqual((e['category'],e['data'][0]),(16,3))
    def test_get_first_and_partial_speed_q_session_flag(self):
        self.assertEqual(self.exchange(0x20c)['data'],bytes([2,6,1,0]))
        for speed in (0,1,2,3,4,255):
            self.assertEqual(self.exchange(0x20b,bytes([1,speed]))['data'],b'\0')
            self.assertEqual(self.exchange(0x20c)['data'][0],speed)
        self.exchange(0x20b,bytes([2,4,3,2,4,2]))
        self.assertEqual(self.exchange(0x20c)['data'],bytes([255,4,2,2]))
        self.assertEqual(self.exchange(0x20b,b'\0')['data'],b'\5')
        self.assertEqual(self.exchange(0x20b,bytes([2,16]))['data'],b'\2')
    def test_filter_error_and_stop_idle(self):
        self.exchange(0x209,bytes([1,1,44,2,0]));self.assertEqual(self.exchange(0x20a)['data'],bytes([1,44,0]))
        e=self.exchange(0x209,bytes([2,1]));self.assertEqual(e['category'],16);self.assertEqual(len(e['data']),6)
        e=self.exchange(0x2ab);self.assertEqual(e['data'],bytes([3,0,2,0xab,0,0]))
        self.assertEqual(self.exchange(0x2ff)['data'],b'\0');self.assertEqual(self.u.receive(128),b'')
    def test_single_inventory_tid_user_and_end(self):
        p=self.exchange(0x210,bytes([0,0,0,1,0,2,0,6,3,0,0,1]));self.assertEqual(p['data'],b'\0')
        tag=checked_frame(self.u.receive(512));end=checked_frame(self.u.receive(512))
        self.assertEqual(tag['category'],18);self.assertEqual(tag['mid'],0)
        self.assertIn(bytes([3,0,12])+bytes(self.fake.banks[2]),tag['data'])
        self.assertIn(bytes([4,0,2,0,0]),tag['data'])
        self.assertEqual((end['category'],end['mid'],end['data']),(18,1,b'\0'))
    def test_continuous_until_stop_and_target_alternates(self):
        self.exchange(0x20b,bytes([4,2]));self.exchange(0x210,bytes([0,0,0,1,1]))
        for _ in range(3): self.assertEqual(checked_frame(self.u.receive(512))['mid'],0)
        self.exchange(0x2ff);end=checked_frame(self.u.receive(512))
        self.assertEqual((end['category'],end['mid'],end['data']),(18,1,b'\1'))
        targets=[w[5] for w in self.fake.writes if w[2]==1]
        self.assertIn(0,targets);self.assertIn(1,targets)
    def test_selected_write_once_and_actual_password_layout(self):
        epc=bytes(self.fake.banks[1][4:16]);selector=bytes([1,0,32,len(epc)*8])+epc
        payload=bytes([0,0,0,1,3,0,0,0,2,0x12,0x34,1,0,len(selector)])+selector+bytes([2,0,0,0,0,3,0])
        self.assertEqual(self.exchange(0x211,payload)['data'],b'\0')
        self.assertEqual(checked_frame(self.u.receive(512))['data'],b'\0')
        self.assertEqual(self.fake.banks[3][:2],bytes.fromhex('1234'))
        self.assertEqual(len([w for w in self.fake.writes if w[2]==3]),1)
        self.assertEqual(self.exchange(0x211,payload[:11])['data'][0],3)
        self.assertEqual(len([w for w in self.fake.writes if w[2]==3]),1)
    def test_write_device_error_returns_nation_status(self):
        epc=bytes(self.fake.banks[1][4:16]);selector=bytes([1,0,32,len(epc)*8])+epc
        payload=bytes([0,0,0,1,3,0,0,0,2,0x12,0x34,1,0,len(selector)])+selector+bytes([2,1,2,3,4])
        self.assertEqual(self.exchange(0x211,payload)['data'],b'\x08')
        self.assertEqual(len([w for w in self.fake.writes if w[2]==3]),1)
    def test_fragmented_and_coalesced_commands(self):
        a=FrameAssembler();x=NRNReader.build_frame(0x202);y=NRNReader.build_frame(0x20c)
        self.assertEqual(a.feed(x[:4]),[]);self.assertEqual(a.feed(x[4:]+y),[x,y])
    def test_standard_public_and_hidden_internal_pair_parsing(self):
        from app import pair_ports
        listing=' CNCA0 PortName=COM#,RealPortName=COM52,EmuOverrun=yes\n CNCB0 PortName=COM53,HiddenMode=yes,dsr=ropen\n'
        self.assertEqual(pair_ports(listing,0),{'A':'COM52','B':'COM53'})
        with self.assertRaises(RuntimeError): pair_ports(' CNCA0 PortName=COM#\n CNCB0 PortName=COM53\n',0)
    def test_busy_public_port_does_not_stop_services(self):
        import tempfile
        from unittest.mock import patch
        import app
        import json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);config=root/'settings.json'
            config.write_text(json.dumps({'public':'COM52','internal':'COM53','pair':'0','reused_old_pair':True}))
            with patch.object(app,'ROOT',root/'install'),patch.object(app,'DATA',root),patch.object(app,'CONFIG',config),patch.object(app.serial,'Serial',side_effect=PermissionError('busy')),patch.object(app,'stop_service') as stop:
                with self.assertRaisesRegex(PermissionError,'busy'): app.install('COM50',lambda *args:None)
                stop.assert_not_called()
    def test_four_antenna_rejected_and_closed(self):
        port=MemorySerial(4);port.reset_input_buffer=port.buffer.clear
        u=QCUART('test',serial_factory=lambda **kw:port)
        with self.assertRaisesRegex(RuntimeError,'1 antenna'): u.open()
        self.assertTrue(port.closed)
    def test_native_passthrough(self):
        class Native:
            timeout=.05
            def __init__(self): self.buffer=bytearray();self.writes=[];self.closed=False
            def reset_input_buffer(self): self.buffer.clear()
            def close(self): self.closed=True
            def read(self,n):
                b=bytes(self.buffer[:n]);del self.buffer[:n]
                if not b: time.sleep(.001)
                return b
            def write(self,raw):
                self.writes.append(raw)
                if raw[0]==0x5a:
                    p=checked_frame(raw);mid=p['category']*256+p['mid']
                    self.buffer.extend(NRNReader.build_frame(mid,b'\0\0' if mid==0x100 else b'\4\5\6'))
                return len(raw)
        port=Native();u=QCUART('native',serial_factory=lambda **kw:port);u.open();self.addCleanup(u.close)
        self.assertEqual(u.backend,'NATION');raw=NRNReader.build_frame(0x2ab,b'\10\11');u.send(raw)
        self.assertEqual(port.writes[-1],raw);self.assertEqual(u.receive(256),NRNReader.build_frame(0x2ab,b'\4\5\6'))


if __name__=='__main__': unittest.main()
