"""Nation wire boundary for the Python COM service, aligned with JS adapter 0.2.2.

No changes to the vendor Nation SDK. ZK 1 antenna only; unsupported commands
produce explicit Nation error notifications. Writes are sent exactly once.
"""
import threading
import time

from zk_nation.adapter import AutoUART, checked_frame
from zk_nation.core.errors import DeviceError, ProtocolError, UnsupportedError, ReaderError
from zk_nation.core.integration import epc_selector

PROFILES = {0:205,1:146,2:141,3:203,4:123,5:103,6:241,7:241,10:146,11:146,12:141,13:141,255:146}
REGIONS = {0:(1,2,16),1:(8,2,16),3:(2,0,50),4:(9,0,4),5:(31,0,4)}


class QCUART(AutoUART):
    def __init__(self, *args, phase=True, fast_tid=True, **kwargs):
        if type(phase) is not bool or type(fast_tid) is not bool:
            raise ValueError('phase and fast_tid must be boolean')
        super().__init__(*args, **kwargs)
        self.phase_enabled = phase
        self.fast_tid_enabled = fast_tid
        self.requested_speed = None
        self.repeat_filter = 0
        self.frequency_mode = None
        self.active = False
        self.request = {}
        self.inventory_stats = {}

    def error(self, mid, code, length):
        self._emit(0, bytes((code,0,mid>>8,mid&255,length>>8,length&255)), notify=True)

    def send(self, raw):
        with self._commands:
            self.last_error = None
            self._trace('APP_TO_NATION', raw)
            if self.ser is None: raise RuntimeError('Serial closed')
            if self.backend == 'NATION':
                if self.ser.write(raw) != len(raw): raise RuntimeError('Partial write; outcome unknown')
                return
            mid = 0
            try:
                p = checked_frame(raw); mid = p['category']*256+p['mid']
                if p['rs485'] or p['notify']: raise UnsupportedError('RS485/notification requests unsupported')
                self._dispatch(mid, p['data'])
            except DeviceError as e:
                self.last_error = str(e)
                if mid == 0x211:
                    status = ({3:6,4:7,11:5}.get(e.data[0],9) if e.status==0xfc and e.data else
                              {0:0,5:8,12:8,16:7,250:10,251:10,248:1,253:3,255:3,11:9,25:9}.get(e.status,11))
                    self._emit(mid, bytes([status]))
                elif mid in (0x201,0x203,0x20b): self._emit(mid,bytes([{0x201:3,0x203:2,0x20b:6}[mid]]))
                else: self.error(mid,9,max(0,len(raw)-9))
            except (ReaderError,ValueError,RuntimeError,OSError) as e:
                self.last_error = str(e)
                self.error(mid,3 if isinstance(e,UnsupportedError) else 7 if isinstance(e,(ValueError,ProtocolError)) else 9,max(0,len(raw)-9))

    def band(self):
        r = self.zk.region()
        candidates = [(n,r['min_channel']-offset,r['max_channel']-offset,count)
            for n,(code,offset,count) in REGIONS.items()
            if r['band']==code and offset<=r['min_channel']<=r['max_channel']<offset+count]
        if len(candidates)!=1: raise UnsupportedError('No unique RF band mapping')
        return candidates[0]

    def _dispatch(self, mid, d):
        if mid == 0x2ff:
            if d: raise ValueError('Stop requires empty payload')
            active = self.active
            self.active = False
            self._stop_worker(); self.flush_input(); self._emit(mid,b'\0')
            if active: self._emit(0x201,b'\1',notify=True)
            return
        if self._running(): raise UnsupportedError('Stop inventory before Get/Set')
        if mid in (0x100,0x200,0x202,0x204,0x206,0x20c,0x20a) and d: raise ValueError('Unexpected query data')
        if mid == 0x100:
            self.device_info=self.zk.get_reader_info(); self._emit(mid,b'\0\0')
        elif mid == 0x200: self._emit(mid,bytes([0,30,1,0,5,0,1,3,4,5,0,1,0]))
        elif mid == 0x10a:
            if d: raise ValueError('Get GPI requires empty data')
            pins=self.zk._call(0x47)
            if len(pins)!=1: raise ProtocolError('Invalid GPIO length')
            self._emit(mid,bytes([1,pins[0]&1]))
        elif mid == 0x202: self._emit(mid,bytes([1,self.zk.get_power()['powers_dbm'][0]]))
        elif mid == 0x201:
            if not d or len(d)%2: raise ValueError('Invalid power PID pairs')
            v=dict(zip(d[::2],d[1::2]))
            if len(v)!=len(d)//2: raise ValueError('Duplicate power PID')
            if set(v)-{1,255} or 1 not in v: self._emit(mid,b'\1'); return
            if v.get(255,1)>1: raise ValueError('Invalid persistence')
            if v[1]>30: self._emit(mid,b'\2'); return
            self.zk.set_power(v[1],antenna=1,persist=bool(v.get(255,1))); self._emit(mid,b'\0')
        elif mid in (0x204,0x206):
            band,first,last,count=self.band(); channels=bytes(range(first,last+1))
            mode=self.frequency_mode if self.frequency_mode is not None else int(len(channels)==count)
            self._emit(mid,bytes([band]) if mid==0x204 else bytes([mode])+len(channels).to_bytes(2,'big')+channels)
        elif mid == 0x203:
            if len(d)!=1 and (len(d)!=3 or d[1]!=1 or d[2]>1): raise ValueError('Invalid RF persistence')
            if d[0] not in REGIONS: self._emit(mid,b'\1'); return
            code,offset,count=REGIONS[d[0]]
            self.zk.region(code,offset,offset+count-1,persist=len(d)==1 or bool(d[2])); self.frequency_mode=1; self._emit(mid,b'\0')
        elif mid == 0x205:
            if not d or d[0]>1: raise UnsupportedError('Invalid frequency mode')
            i=1; channels=None; persist=False; seen=set()
            while i<len(d):
                pid=d[i];i+=1
                if pid in seen: raise ValueError('Duplicate frequency option')
                seen.add(pid)
                if pid==1 and i+2<=len(d):
                    size=int.from_bytes(d[i:i+2],'big');i+=2;channels=list(d[i:i+size]);i+=size
                    if len(channels)!=size: raise ValueError('Truncated channels')
                elif pid==2 and i<len(d) and d[i]<=1: persist=bool(d[i]);i+=1
                else: raise UnsupportedError('Frequency option unsupported')
            band,*_=self.band(); code,offset,count=REGIONS[band]
            if d[0]==1: channels=list(range(count))
            else:
                channels=sorted(channels or [])
                if not channels or any(n>=count or (j and n!=channels[j-1]+1) for j,n in enumerate(channels)):
                    raise UnsupportedError('ZK requires unique contiguous channels')
            self.zk.region(code,offset+channels[0],offset+channels[-1],persist=persist)
            self.frequency_mode=d[0];self._emit(mid,b'\0')
        elif mid == 0x20c:
            profile=self.zk.profile(format='extended')['profile_id'];query=self.zk.query()
            candidates=[n for n,p in PROFILES.items() if p==profile]
            if not candidates: raise UnsupportedError('Profile has no Nation mapping')
            exact=[n for n in candidates if n in (4,5,6)]
            speed=self.requested_speed if self.requested_speed in candidates else exact[0] if len(exact)==1 else min(candidates)
            self._emit(mid,bytes([speed,query['q'],query['session'],self._target]))
        elif mid == 0x20b:
            v=dict(zip(d[::2],d[1::2])); invalid=not d or len(d)%2 or len(v)!=len(d)//2 or set(v)-{1,2,3,4,255} or not set(v)&{1,2,3,4}
            if invalid: self._emit(mid,b'\5'); return
            if v.get(255,0): raise UnsupportedError('Baseband persistence unsupported')
            for pid,valid in ((1,v.get(1) in PROFILES),(2,v.get(2,0)<=15),(3,v.get(3,0)<=3),(4,v.get(4,0)<=2)):
                if pid in v and not valid: self._emit(mid,bytes([pid])); return
            self.zk.profile(format='extended');self.zk.query() # Read-only preflight.
            if 1 in v: self.zk.profile(PROFILES[v[1]],format='extended',persist=False);self.requested_speed=v[1]
            if 2 in v or 3 in v: self.zk.query(q=v.get(2),session=v.get(3),persist=False)
            if 4 in v: self._target=v[4]
            self._emit(mid,b'\0')
        elif mid == 0x209:
            i=0;seen=set();repeat=self.repeat_filter;threshold=0
            if not d: raise ValueError('Filter requires PID')
            while i<len(d):
                pid=d[i];i+=1
                if pid in seen: raise ValueError('Duplicate filter PID')
                seen.add(pid)
                if pid==1 and i+2<=len(d): repeat=int.from_bytes(d[i:i+2],'big');i+=2
                elif pid==2 and i<len(d): threshold=d[i];i+=1
                else: raise ValueError('Invalid filter PID')
            if threshold: raise UnsupportedError('No verified RSSI scale')
            self.repeat_filter=repeat;self._emit(mid,b'\0')
        elif mid == 0x20a: self._emit(mid,self.repeat_filter.to_bytes(2,'big')+b'\0')
        elif mid == 0x210:
            self.request=self._inventory_request(d);query=self.zk.query()
            if query['session']==255: raise UnsupportedError('Auto Session unsupported')
            self._pc={};self.last_inventory_error=None;self._stop.clear();self.active=True
            self.inventory_stats=dict(reports=0,phase_reports=0,fast_tid_reports=0,tid_read_commands=0)
            self._emit(mid,b'\0')
            self._worker=threading.Thread(target=self._inventory,args=(query,bool(d[4])),daemon=True);self._worker.start()
        elif mid == 0x211:
            self._write(d);self._pc.clear();self._emit(mid,b'\0');self._emit(0x201,b'\0',notify=True)
        else: raise UnsupportedError(f'Nation MID 0x{mid:04X} unsupported')

    def _inventory_request(self,d):
        if len(d)<5 or d[:4]!=b'\0\0\0\1' or d[4]>1: raise UnsupportedError('Inventory requires ANT1')
        r={'reads':[],'password':'00000000'};i=5;seen=set()
        while i<len(d):
            pid=d[i];i+=1
            if pid in seen: raise ValueError('Duplicate inventory field')
            seen.add(pid)
            if pid==1:
                if i+2>len(d): raise ValueError('Truncated selector')
                size=int.from_bytes(d[i:i+2],'big');i+=2;v=d[i:i+size];i+=size
                if len(v)!=size or size<6 or v[:3]!=b'\1\0\x20' or v[3]!=8*(size-4): raise UnsupportedError('Full EPC bit32 selector required')
                r['epc']=v[4:].hex().upper()
            elif pid==2:
                if i+2>len(d) or d[i]>1 or not 1<=d[i+1]<=32: raise UnsupportedError('TID requires 1..32 words')
                r['reads'].append((3,2,0,d[i+1]));i+=2
            elif pid in (3,4):
                if i+3>len(d) or d[i]!=0 or not 1<=d[i+2]<=32: raise UnsupportedError('Memory requires word0..255, 1..32 words')
                r['reads'].append((pid+1,3 if pid==3 else 0,d[i+1],d[i+2]));i+=3
            elif pid==5:
                if i+4>len(d): raise ValueError('Truncated password')
                r['password']=d[i:i+4].hex();i+=4
            else: raise UnsupportedError('Inventory option unsupported')
        return r

    def _write(self,d):
        if len(d)<11 or d[:4]!=b'\0\0\0\1': raise UnsupportedError('Write requires ANT1')
        bank=d[4];word=int.from_bytes(d[5:7],'big');size=int.from_bytes(d[7:9],'big')
        if bank not in (1,3) or not 0<size<=64 or size%2 or 9+size>len(d) or word+size//2>65536 or (bank==1 and word==0): raise UnsupportedError('Invalid EPC/User write')
        selector=None;pwd=b'\0'*4;i=9+size;seen=set()
        while i<len(d):
            pid=d[i];i+=1
            if pid in seen: raise ValueError('Duplicate write option')
            seen.add(pid)
            if pid==2:
                if i+4>len(d): raise ValueError('Truncated password')
                pwd=d[i:i+4];i+=4
            elif pid==3:
                if i>=len(d): raise ValueError('Truncated block mode')
                if d[i]!=0: raise UnsupportedError('BlockWrite unsupported')
                i+=1
            elif pid==1:
                if i+2>len(d): raise ValueError('Truncated selector')
                n=int.from_bytes(d[i:i+2],'big');i+=2;v=d[i:i+n];i+=n
                if len(v)!=n or n<5 or v[3]!=(n-4)*8 or v[0]>3 or (v[0]==1 and int.from_bytes(v[1:3],'big')<32): raise UnsupportedError('Invalid selector')
                selector=v
            else: raise UnsupportedError('Write option unsupported')
        if selector is None: raise UnsupportedError('Selected write required')
        payload=bytes([size//2,255,bank])+word.to_bytes(2 if word>255 else 1,'big')+d[9:9+size]+pwd+selector
        reply=self.zk._call(0x16 if word>255 else 3,payload)
        if reply: raise ProtocolError('Unexpected write reply; verify before retry')

    def _inventory(self,query,continuous):
        reason=0;round_index=0;last={}
        # FastID returns 12 TID bytes; longer/password-protected reads use the
        # selected-read path. Never cache a TID from a previous tag/report.
        fast_tid = self.fast_tid_enabled and self.request['password']=='00000000' and any(
            bank==2 and word==0 and words<=6 for _,bank,word,words in self.request['reads'])
        try:
            while not self._stop.is_set():
                target=round_index%2 if self._target==2 else self._target
                batch=self.zk.inventory_once(antennas=[1],q=query['q'],session=query['session'],target=target,
                    phase=self.phase_enabled,fast_tid=fast_tid);round_index+=1
                for tag in batch['tags']:
                    if self._stop.is_set(): break
                    epc=tag['epc']
                    if self.request.get('epc',epc)!=epc: continue
                    now=time.monotonic()
                    if self.repeat_filter and now-last.get(epc,-float('inf'))<self.repeat_filter/100: continue
                    selector=epc_selector(epc);pc=self._pc.get(epc)
                    if pc is None:
                        try: pc=bytes.fromhex(self.zk.read_tag(antenna=1,bank=1,word_address=1,words=1,selector=selector)['hex'])
                        except DeviceError as e:
                            self.last_error=f'PC unavailable for {epc}: {e}';self.logger.warning(self.last_error);continue
                        if len(self._pc)>=10000: raise ProtocolError('PC cache limit')
                        self._pc[epc]=pc
                    epc_bytes=bytes.fromhex(epc);memory=bytearray();result=0;used_fast_tid=False
                    for pid,bank,word,words in self.request['reads']:
                        try:
                            if fast_tid and bank==2 and word==0 and words<=6 and 'tid' in tag:
                                data=bytes.fromhex(tag['tid'])[:words*2];used_fast_tid=True
                            else:
                                if bank==2: self.inventory_stats['tid_read_commands']+=1
                                data=bytes.fromhex(self.zk.read_tag(antenna=1,bank=bank,word_address=word,words=words,selector=selector,password=self.request['password'])['hex'])
                            memory.extend(bytes([pid])+len(data).to_bytes(2,'big')+data)
                        except DeviceError as e:
                            result=5 if e.status==5 else 1 if e.status in (250,251) else {3:4,4:3}.get(e.data[0],6) if e.status==252 and e.data else 7
                            self.last_error=str(e)
                    if self.phase_enabled and 'phase_end_degrees' in tag:
                        # Nation PID09 is 2*pi/128 per unit, one current phase.
                        # Ex10 has begin/end: use end, quantized to Nation units.
                        phase_raw=int(tag['phase_end_degrees']*128/360+0.5)
                        memory.extend(bytes([8])+tag['frequency_khz'].to_bytes(4,'big')+bytes([9,phase_raw]))
                        self.inventory_stats['phase_reports']+=1
                    self._emit(0x200,len(epc_bytes).to_bytes(2,'big')+epc_bytes+pc+bytes([1,2,result])+memory,notify=True);last[epc]=now
                    self.inventory_stats['reports']+=1
                    if used_fast_tid: self.inventory_stats['fast_tid_reports']+=1
                if not continuous and (self._target!=2 or round_index>=2): break
                self._stop.wait(.001)
        except (ReaderError,ValueError,RuntimeError,OSError) as e:
            reason=2;self.last_inventory_error=str(e);self.logger.error('Inventory ended: %s',e)
        finally:
            self.logger.info('INVENTORY_STATS %s',self.inventory_stats)
            if self.active:
                self.active=False;self._emit(0x201,bytes([reason]),notify=True)
