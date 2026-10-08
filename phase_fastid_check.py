"""Phase/FastID QC. Inventory only; optional temporary CFG9 phase Set/Get.

No writes to tags, RF band or power. --cfg-check changes RAM CFG9, restores it.
Disconnect other applications before testing the physical COM.
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
from hardware_check import PublicUART


def run(port,output,public=False,cfg_check=False):
    u=PublicUART(port) if public else QCUART(port,phase=True,fast_tid=True)
    result={'port':port,'status':'RUNNING','frames':[],'modes':[]};snapshot=None
    parser=NRNReader('parser-only',115200);inflight_reports=0
    def receive(timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            raw=u.receive(65536)
            if raw:
                result['frames'].append({'rx':raw.hex().upper()});return checked_frame(raw)
        raise TimeoutError('No Nation frame')
    def request(mid,data=b''):
        nonlocal inflight_reports
        raw=NRNReader.build_frame(mid,data);result['frames'].append({'tx':raw.hex().upper()});u.send(raw)
        p=receive()
        # Serial may already contain valid notifications before Stop ACK.
        while mid==0x2ff and (p['category'],p['mid'])==(18,0):
            inflight_reports+=1;p=receive()
        assert (p['category'],p['mid'])==(mid>>8,mid&255),p
        return p['data']
    try:
        u.open();result['backend']=u.backend
        if cfg_check:
            if public: raise ValueError('--cfg-check requires direct physical COM')
            snapshot=u.zk.query();result['cfg9_before']=snapshot
            changed=u.zk.query(phase=True,persist=False)
            assert changed['phase'] and changed['q']==snapshot['q'] and changed['session']==snapshot['session']
            assert request(0x20c)[1]==snapshot['q']
            changed_q=(snapshot['q']+1)%16
            assert request(0x20b,bytes([2,changed_q]))==b'\0'
            assert u.zk.query()['phase'] and u.zk.query()['q']==changed_q
            result['cfg9_phase_and_partial_q']='PASS'
            u.zk.query(q=snapshot['q'],session=snapshot['session'],phase=snapshot['phase'],fast_tid=snapshot['fast_tid'])
        modes=[('installed',True,True)] if public else [('legacy',False,False),('phase',True,False),('phase_fastid',True,True)]
        for name,phase,fast in modes:
            if not public: u.phase_enabled=phase;u.fast_tid_enabled=fast
            assert request(0x210,b'\0\0\0\1\1\2\0\6')==b'\0'
            tags=[];deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                p=receive()
                assert (p['category'],p['mid'])==(18,0),p
                tag=parser.parse_epc(p['data']);assert 'error' not in tag,tag;tags.append(tag)
            assert request(0x2ff)==b'\0'
            end=receive();assert (end['category'],end['mid'],end['data'])==(18,1,b'\1'),end
            assert tags and any('tid' in tag for tag in tags)
            if phase:
                assert any('phase' in tag and 'frequency' in tag for tag in tags)
                assert all(800<=tag['frequency']<=1000 for tag in tags if 'frequency' in tag)
            else: assert all('phase' not in tag and 'frequency' not in tag for tag in tags)
            stats={} if public else dict(u.inventory_stats)
            if fast and not public: assert stats['fast_tid_reports']>0,stats
            result['modes'].append({'name':name,'count':len(tags),'stop_inflight_reports':inflight_reports,'stats':stats,'samples':tags[:5]})
            inflight_reports=0
        result['status']='PASS'
    except Exception as e:
        result['status']='FAIL';result['error']=repr(e);raise
    finally:
        try:
            if not public and u.active: request(0x2ff);receive()
            if snapshot:
                restored=u.zk.query(q=snapshot['q'],session=snapshot['session'],phase=snapshot['phase'],fast_tid=snapshot['fast_tid'])
                assert restored==snapshot
                result['cfg9_restored']=restored
        finally:
            u.close();Path(output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='frames'},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',default='COM50');p.add_argument('--public',action='store_true')
    p.add_argument('--cfg-check',action='store_true');p.add_argument('--output',default='phase-fastid-results.json')
    a=p.parse_args();run(a.port,a.output,a.public,a.cfg_check)
