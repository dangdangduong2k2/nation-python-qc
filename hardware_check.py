"""Explicit test-tag QC. Modifies then restores RAM parameters and User word0.

Never run against production tags or while another application holds the COM.
"""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).parent/'src'))
from qc_uart import QCUART
from zk_nation.vendor.nrn import NRNReader
from zk_nation.adapter import checked_frame

class PublicUART:
    backend='NATION-COM'
    def __init__(self,port): self.port=port;self.ser=None;self.active=False;self.queue=[]
    def open(self):
        import serial
        from service_bridge import FrameAssembler
        self.assembly=FrameAssembler();self.ser=serial.Serial(self.port,115200,timeout=.05,write_timeout=2)
    def send(self,raw):
        if self.ser.write(raw)!=len(raw): raise IOError('Partial public COM write')
    def receive(self,size):
        if not self.queue: self.queue.extend(self.assembly.feed(self.ser.read(max(1,self.ser.in_waiting))))
        return self.queue.pop(0) if self.queue else b''
    def close(self):
        if self.ser: self.ser.close();self.ser=None


def run(port,output,public=False):
    u=PublicUART(port) if public else QCUART(port,allow_approximate=True)
    events=[];snapshot={};tag=None;user_original=None;changed_user=False
    def receive(timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            raw=u.receive(65536)
            if raw:
                p=checked_frame(raw);events.append({'rx':raw.hex().upper()});return p
        raise TimeoutError('No Nation frame')
    def req(mid,data=b''):
        raw=NRNReader.build_frame(mid,data);events.append({'tx':raw.hex().upper()});u.send(raw);p=receive()
        assert (p['category'],p['mid'])==(mid>>8,mid&255),p
        return p['data']
    def scan(extra=b''):
        assert req(0x210,b'\0\0\0\1\0'+extra)==b'\0';tags=[]
        while True:
            p=receive()
            if p['category']==18 and p['mid']==1:
                assert p['data']==b'\0',p;return tags
            if p['category']==18 and p['mid']==0: tags.append(p['data'])
    def user_from(data):
        n=int.from_bytes(data[:2],'big');i=2+n+3
        while i<len(data):
            pid=data[i];i+=1
            if pid==2: assert data[i]==0; i+=1
            elif pid==8: i+=4
            elif pid==9: i+=1
            elif pid in (3,4,5):
                n=int.from_bytes(data[i:i+2],'big');i+=2;v=data[i:i+n];i+=n
                if pid==4:return v
            else:raise AssertionError(('unexpected tag PID',pid))
        raise AssertionError('No User data')
    def write_user(epc,data):
        selector=bytes([1,0,32,len(epc)*8])+epc
        payload=b'\0\0\0\1\3\0\0'+len(data).to_bytes(2,'big')+data+bytes([1,0,len(selector)])+selector+b'\2\0\0\0\0'
        assert req(0x211,payload)==b'\0'
        p=receive();assert (p['category'],p['mid'],p['data'])==(18,1,b'\0')
    result={'port':port,'tests':[],'status':'RUNNING'}
    try:
        u.open();result['backend']=u.backend;assert u.backend==('NATION-COM' if public else 'ZK')
        snapshot['power']=req(0x202);snapshot['band']=req(0x204);snapshot['frequency']=req(0x206);snapshot['baseband']=req(0x20c)
        result['tests'].append('Get before Set: power/RF band/frequency/baseband')
        assert req(0x201,bytes([1,18,255,0]))==b'\0';assert req(0x202)==bytes([1,18])
        result['tests'].append('Set/Get power 18dBm')
        for band in (0,1,3,4,5):
            assert req(0x203,bytes([band,1,0]))==b'\0';assert req(0x204)==bytes([band])
        result['tests'].append('Set/Get RF bands 0/1/3/4/5')
        req(0x203,b'\0\1\0');req(0x205,bytes([0,1,0,3,1,2,3,2,0]));assert req(0x206)==bytes([0,0,3,1,2,3])
        result['tests'].append('Set/Get contiguous frequency subset')
        req(0x203,snapshot['band']+b'\1\0')
        for speed in (0,1,2,3,4,255):
            assert req(0x20b,bytes([1,speed,2,4,3,0,4,0]))==b'\0';assert req(0x20c)==bytes([speed,4,0,0])
        for flag in (0,1,2):
            assert req(0x20b,bytes([4,flag]))==b'\0';assert req(0x20c)[3]==flag
        result['tests'].append('Set/Get speeds 0/1/2/3/4/255 and Q/Session/Flag')
        req(0x20b,bytes([1,snapshot['baseband'][0],2,snapshot['baseband'][1],3,0,4,0]))
        req(0x20b,b'\0');assert req(0x20c)[2:]==b'\0\0' # malformed preset leaves real config alone.
        data=scan(bytes([2,0,6,3,0,0,1]));assert data,'No test tag on ANT1'
        tag=data[0];n=int.from_bytes(tag[:2],'big');epc=tag[2:2+n];result['epc']=epc.hex().upper();user_original=user_from(tag)
        result['tests'].append('Single inventory EPC/PC/TID/User + correct 1201 end')
        changed_user=True;write_user(epc,b'\xA5\x5A')
        selector=bytes([1,0,32,len(epc)*8])+epc
        selected=bytes([1,0,len(selector)])+selector+bytes([3,0,0,1])
        data=scan(selected);assert len(data)==1 and user_from(data[0])==b'\xA5\x5A'
        write_user(epc,user_original);changed_user=False
        assert user_from(scan(selected)[0])==user_original
        result['tests'].append('Selected User write A55A, readback, restore, verify')
        assert req(0x210,b'\0\0\0\1\1')==b'\0'
        deadline=time.monotonic()+5;count=0
        while time.monotonic()<deadline:
            p=receive();assert p['category']==18 and p['mid']==0;count+=1
        assert req(0x2ff)==b'\0';p=receive();assert (p['category'],p['mid'],p['data'])==(18,1,b'\1')
        result['tests'].append(f'Continuous inventory 5s: {count} reports; Stop ACK + end1')
        result['status']='PASS'
    except Exception as e:
        result['status']='FAIL';result['error']=str(e);raise
    finally:
        try:
            if u.ser is not None:
                req(0x2ff)
                if changed_user and tag is not None:
                    n=int.from_bytes(tag[:2],'big');write_user(tag[2:2+n],user_original)
                if snapshot:
                    req(0x201,snapshot['power']+b'\xff\0')
                    req(0x203,snapshot['band']+b'\1\0')
                    f=snapshot['frequency'];req(0x205,bytes([f[0],1])+f[1:3]+f[3:]+bytes([2,0]))
                    b=snapshot['baseband'];req(0x20b,bytes([1,b[0],2,b[1],3,b[2],4,b[3]]))
                    result['restored']={k:req(m).hex() for k,m in (('power',0x202),('band',0x204),('frequency',0x206),('baseband',0x20c))}
                    assert result['restored']=={k:v.hex() for k,v in snapshot.items()}
                    result['restoration']='PASS'
        except Exception as e:
            result['restoration']='FAIL: '+str(e);result['status']='FAIL'
        u.close();result['frames']=events;Path(output).write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='frames'},indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',required=True);p.add_argument('--output',default='hardware-results.json');p.add_argument('--test-tag-authorized',action='store_true',required=True);p.add_argument('--public',action='store_true');a=p.parse_args()
    run(a.port,a.output,a.public)
