"""Auto-detect physical Nation/ZK; expose Nation frames to the original NRN SDK.

No virtual COM or server. Single reader connection, no write replay.
The public UART boundary is open/close/send/receive/flush_input.
"""
from collections import deque
import logging
import threading
import time

import serial
from .vendor.nrn import NRNReader
from .core.nation import NationReader, MATCHING_PROFILES
from .core.models import antenna_count
from .core.protocol import SerialTransport, command_frame, decode_response
from .core.errors import ReaderError, UnsupportedError, ValidationError, ProtocolError
from .core.nation_errors import map_nation_error
from .core.integration import epc_selector, error_details
from .radio import RadioMapper, SPEEDS, BANDS


class DetectionError(RuntimeError):
    pass


def checked_frame(raw):
    parsed = NRNReader.parse_frame(raw)
    expected = 9 + int(parsed['rs485']) + parsed['data_length']
    if len(raw) != expected or parsed['proto_type'] != 0 or parsed['proto_ver'] != 1:
        raise ValueError('Invalid Nation frame length/version')
    return parsed


def _read_exact(port, size, deadline):
    data = bytearray()
    while len(data) < size and time.monotonic() < deadline:
        data.extend(port.read(size-len(data)))
    return bytes(data)


class AutoUART:
    def __init__(self, port, baudrate=115200, timeout=0.15, logger=None, *,
                 allow_approximate=False, allow_approximate_rf=False, trace=None, serial_factory=None):
        self.port_name, self.baudrate, self.timeout = port, baudrate, timeout
        self.logger = logger or logging.getLogger('auto_uart')
        self.allow_approximate, self.trace = allow_approximate, trace
        self.allow_approximate_rf = allow_approximate_rf
        self.radio = None
        self.serial_factory = serial_factory or serial.Serial
        self.backend = None
        self.ser = self.zk = None
        self.device_info = None
        self.last_error = None
        self.last_inventory_error = None
        self.mapping_notes = {}
        self._condition = threading.Condition()
        self._commands = threading.RLock()
        self._rx = bytearray()
        self._frame_sizes = deque()
        self._stop = threading.Event()
        self._worker = None
        self._pc = {}
        self._target = 0

    def _trace(self, direction, raw):
        if self.trace:
            try:
                self.trace(direction, bytes(raw))
            except Exception as error:
                self.logger.warning('Trace callback: %s', error)

    def open(self):
        if self.ser is not None:
            raise RuntimeError('Already open')
        self.last_error = None
        self.last_inventory_error = None
        self.device_info = None
        self.mapping_notes = {}
        self._target = 0
        self._pc.clear()
        self.flush_input()
        self.ser = self.serial_factory(port=self.port_name, baudrate=self.baudrate,
            timeout=0.05, write_timeout=1, bytesize=8, parity='N', stopbits=1,
            xonxoff=False, rtscts=False, dsrdtr=False)
        try:
            self.ser.reset_input_buffer()
            probe = command_frame(255, 0x21)
            self.ser.write(probe)  # GetInfo only, never scan/reset/set during detection.
            deadline = time.monotonic()+0.7
            first = _read_exact(self.ser, 1, deadline)
            reply = None
            if first and first[0] >= 5:
                data = first + _read_exact(self.ser, first[0], deadline)
                try:
                    candidate = decode_response(data)
                    if candidate.command == 0x21 and candidate.status == 0 and len(candidate.data) == 12 and candidate.address != 255:
                        reply = candidate
                except ProtocolError:
                    pass
            if reply is not None:
                antennas = antenna_count(reply.data[2])
                if antennas != 1:
                    raise DetectionError('Detected ZK, but this test package validates 1 antenna only')
                transport = SerialTransport(self.ser, address=reply.address, timeout=3)
                self.zk = NationReader(transport, antennas=1)
                self.radio = RadioMapper(self.zk)
                self.backend = 'ZK'
                self.device_info = {'reader_type': reply.data[2],
                    'firmware': f'{reply.data[0]}.{reply.data[1]}', 'antennas': antennas}
                return
            # A failed ZK probe is NOT evidence of Nation. Require a CRC-valid GetInfo response.
            self.ser.reset_input_buffer()
            request = NRNReader.build_frame(0x0100)
            self.ser.write(request)
            deadline = time.monotonic()+1.5
            buffer = bytearray()
            while time.monotonic() < deadline:
                buffer.extend(self.ser.read(128))
                while buffer:
                    if buffer[0] != 0x5a:
                        del buffer[0]
                        continue
                    if len(buffer) < 7:
                        break
                    extra = 1 if buffer[3] & 0x20 else 0
                    size = 9+extra+int.from_bytes(buffer[5+extra:7+extra], 'big')
                    if size > 4096:
                        del buffer[0]
                        continue
                    if len(buffer) < size:
                        break
                    raw = bytes(buffer[:size]); del buffer[:size]
                    try:
                        p = checked_frame(raw)
                    except ValueError:
                        continue
                    if p['category'] == 1 and p['mid'] == 0 and not p['notify'] and len(p['data']) >= 2:
                        self.backend = 'NATION'
                        self.device_info = NRNReader._parse_query_info_data(p['data'])
                        self.ser.reset_input_buffer()
                        return
            raise DetectionError('No valid Nation or ZK GetInfo response; check COM/baud/cable/other apps')
        except BaseException:
            self.close()
            raise

    def _emit(self, mid, payload=b'', *, notify=False):
        raw = NRNReader.build_frame(mid, bytes(payload), notify=notify)
        self._trace('NATION_TO_APP', raw)
        with self._condition:
            if len(self._rx)+len(raw) > 1048576:
                raise ProtocolError('Nation output buffer full; reader stopped, no silent dropping')
            self._rx.extend(raw)
            self._frame_sizes.append(len(raw))
            self._condition.notify_all()

    def _running(self):
        return self._worker is not None and self._worker.is_alive()

    def _stop_worker(self):
        self._stop.set()
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join()

    def send(self, raw):
        with self._commands:
            self.last_error = None
            p = checked_frame(raw)
            if self.ser is None:
                raise RuntimeError('Serial closed')
            self._trace('APP_TO_NATION', raw)
            if self.backend == 'NATION':
                if self.ser.write(raw) != len(raw):
                    raise RuntimeError('Short write; outcome unknown, do not retry automatically')
                return
            mid = p['category']*256+p['mid']
            try:
                # Upstream query_rfid_ability explicitly uses CAT=0x10, which
                # its general parser also labels as notify. Accept that one
                # known query without modifying the SDK's frame definition.
                if p['rs485'] or (p['notify'] and mid != 0x1000):
                    raise UnsupportedError('RS485 and app notification frames are unsupported')
                self._dispatch(mid, p['data'])
            except (ReaderError, ValueError, RuntimeError, OSError) as error:
                self.last_error = error_details(error)
                op = {0x201:'set_power',0x203:'set_region',0x20b:'set_baseband',
                      0x211:'write_tag',0x210:'inventory'}.get(mid)
                if op and isinstance(error, (ReaderError, ValueError, OSError)) and not isinstance(error, UnsupportedError):
                    mapped = map_nation_error(op, error)
                    self.last_error['nation'] = mapped.as_dict()
                    if mapped.scope == 'command_result' and mapped.code is not None:
                        self._emit(mid, bytes([mapped.code]))
                    else:
                        self._emit(0, bytes([mapped.code if mapped.scope == 'protocol_error' and mapped.code is not None else 9]))
                else:
                    self._emit(0, bytes([3 if isinstance(error, UnsupportedError) else 7 if isinstance(error, ValueError) else 9]))

    def _dispatch(self, mid, data):
        if mid == 0x2ff:
            self._stop_worker()
            self.flush_input()
            self._emit(mid, b'\0')
            return
        if self._running():
            raise UnsupportedError('Stop inventory before issuing another command')
        empty_queries = {0x100,0x1000,0x202,0x204,0x206,0x20c,0x20a}
        if mid in empty_queries and data:
            raise ValueError('Unexpected query payload')
        if mid == 0x100:
            self.device_info = self.zk.get_reader_info()
            # No serial/uptime/build value available in Ex10 GetInfo. Minimal valid
            # partial payload; actual ZK metadata is exposed separately, not invented.
            self._emit(mid, b'\0\0')
        elif mid == 0x1000:
            self._emit(mid, bytes([30,0,1,9,*range(9),1,0]))
        elif mid == 0x202:
            self._emit(mid, bytes([1,self.zk.get_power()['powers_dbm'][0]]))
        elif mid == 0x201:
            if not data or len(data)%2:
                raise ValueError('Power payload must be PID/value pairs')
            pairs = list(zip(data[::2],data[1::2]))
            if len({pid for pid,_ in pairs}) != len(pairs):
                raise ValueError('Duplicate power PID')
            values = dict(pairs)
            if any(k not in (1,255) for k in values) or 1 not in values:
                raise ValidationError('antenna','Only ANT1 is available')
            if values.get(255,1) not in (0,1):
                raise ValueError('Invalid persistence flag')
            self.zk.set_power(values[1],antenna=1,persist=bool(values.get(255,1)))
            self._emit(mid,b'\0')
        elif mid in (0x204,0x206):
            result = self.radio.get()
            self.mapping_notes['rf_band'] = result
            count = sum(end-start+1 for _,start,end in result['legs'])
            self._emit(mid,bytes([result['nation_band']]) if mid==0x204 else bytes([1,*range(count)]))
        elif mid == 0x203:
            if len(data)!=1 or data[0] not in BANDS:
                raise ValidationError('nation_band','Supported Nation bands: 0..8')
            # The original SDK ignores its persist argument on the wire.
            # Explicit bridge policy: region changes are RAM-only on ZK.
            self.mapping_notes['rf_band'] = self.radio.set(data[0],allow_approximate=self.allow_approximate_rf)
            self._emit(mid,b'\0')
        elif mid == 0x20c:
            result = self.zk.get_baseband()
            candidates = result['epc_speed_candidates']
            # Read-only Get does not require permission to change RF.
            # Canonical 4 represents the actual 320kHz; never report Auto=255
            # for a fixed ZK preset. All differences remain in metadata.
            profile = result['zk_profile']['profile_id']
            canonical = {205:0,146:1,141:4,203:3,241:4,7:1,5:4}
            if profile not in canonical:
                self.mapping_notes['baseband'] = {'zk':result,'wire_speed':None,'approximate':True}
                raise UnsupportedError('ZK profile has no verified Nextwaves speed representation')
            speed = canonical[profile]
            self.mapping_notes['baseband'] = {'zk':result,'wire_speed':speed,
                'approximate':speed not in MATCHING_PROFILES,'candidates':candidates}
            self.mapping_notes['baseband']['actual_rf'] = SPEEDS[speed]['actual']
            self.mapping_notes['baseband']['target_mode'] = ['A','B','host_alternating'][self._target]
            self._emit(mid,bytes([speed,result['q'],result['session'],self._target]))
        elif mid == 0x20b:
            if len(data)!=8 or data[::2]!=bytes([1,2,3,4]):
                raise ValueError('Expected speed/Q/Session/target TLV')
            speed,q,session,target=data[1::2]
            if target not in (0,1,2):
                raise ValidationError('target','Target must be A, B, or alternating A/B')
            self.zk.set_baseband(epc_speed=speed,q=q,session=session,
                                 allow_approximate=self.allow_approximate,persist=False)
            self._target=target
            self._emit(mid,b'\0')
        elif mid == 0x20a:
            self._emit(mid,b'\0\0\0')  # No host repeat filter / RSSI threshold.
        elif mid == 0x210:
            if len(data)!=5 or int.from_bytes(data[:4],'big')!=1 or data[4] not in (0,1):
                raise ValidationError('antenna','Select ANT1; optional memory fields are unsupported')
            query = self.zk.query()
            if query['session']==255:
                raise UnsupportedError('Auto Session is not supported by ordinary inventory')
            self._pc={}
            self.last_inventory_error=None
            self._stop.clear()
            self._emit(mid,b'\0')
            self._worker=threading.Thread(target=self._inventory,args=(query,bool(data[4])),daemon=False)
            self._worker.start()
        elif mid == 0x211:
            self._write(data)
            self._pc.clear()
            self._emit(mid,b'\0')
        else:
            raise UnsupportedError(f'Nation command 0x{mid:04X} is not mapped')

    def _write(self, data):
        if len(data)<11 or int.from_bytes(data[:4],'big')!=1:
            raise ValidationError('antenna','Selected write requires ANT1')
        bank,word = data[4],int.from_bytes(data[5:7],'big')
        size=int.from_bytes(data[7:9],'big')
        if size<2 or size%2 or 9+size>len(data):
            raise ValidationError('hex','Invalid whole-word write length')
        content,remaining=data[9:9+size],data[9+size:]
        selector,password=None,'00000000'
        seen=set()
        while remaining:
            if len(remaining)<3:
                raise ValidationError('selector','Truncated optional field')
            pid,n=remaining[0],int.from_bytes(remaining[1:3],'big')
            if pid in seen or n>len(remaining)-3:
                raise ValidationError('selector','Duplicate/truncated optional field')
            seen.add(pid)
            value,remaining=remaining[3:3+n],remaining[3+n:]
            if pid==1 and len(value)>=5 and value[3]==len(value[4:])*8:
                selector={'bank':value[0],'bit_address':int.from_bytes(value[1:3],'big')*16,'hex':value[4:].hex()}
                # The upstream auto writer places an EPC-only mask at PC word1.
                # Reject this ambiguous request rather than rewriting its meaning.
                if selector['bank']==1 and selector['bit_address']<32:
                    raise ValidationError('selector','EPC match must start at word2; upstream auto-write mask is ambiguous')
            elif pid==2 and len(value)==4:
                password=value.hex()
            else:
                raise ValidationError('selector','Unsupported optional field')
        if selector is None:
            raise ValidationError('selector','Unselected writes are not supported')
        self.zk.write_tag(antenna=1,bank=bank,word_address=word,hex=content.hex(),selector=selector,password=password)

    def _inventory(self, query, continuous):
        try:
            round_index = 0
            while not self._stop.is_set():
                self.radio.before_round()
                target = round_index % 2 if self._target==2 else self._target
                batch=self.zk.inventory_once(antennas=[1],q=query['q'],session=query['session'],target=target)
                round_index += 1
                for tag in batch['tags']:
                    if self._stop.is_set():
                        break
                    epc=tag['epc']
                    pc=self._pc.get(epc)
                    if pc is None:
                        pc=bytes.fromhex(self.zk.read_tag(antenna=1,bank=1,word_address=1,words=1,selector=epc_selector(epc))['hex'])
                        if len(self._pc)>=10000:
                            raise ProtocolError('PC cache limit exceeded')
                        self._pc[epc]=pc
                    epc_bytes=bytes.fromhex(epc)
                    payload=len(epc_bytes).to_bytes(2,'big')+epc_bytes+pc+b'\x01'
                    # No Nation RSSI PID: the ZK raw number is not Nation's RSSI scale.
                    self._emit(0x0200,payload,notify=True)
                if not continuous:
                    self._emit(0x0231,b'\0',notify=True)
                    return
                self._stop.wait(0.001)
        except (ReaderError,ValueError,RuntimeError,OSError) as error:
            self.last_error=self.last_inventory_error=error_details(error)
            with self._condition:
                if len(self._rx)>1048500:
                    self._rx.clear()
                    self._frame_sizes.clear()
            self._emit(0x0231,b'\x02',notify=True)
            self._stop.set()

    def receive(self,size=64):
        if self.ser is None:
            raise RuntimeError('Serial closed')
        if self.backend=='NATION':
            raw=self.ser.read(size)
            if raw:self._trace('NATION_TO_APP',raw)
            return raw
        deadline=time.monotonic()+self.timeout
        with self._condition:
            while not self._rx and self.ser is not None:
                remaining=deadline-time.monotonic()
                if remaining<=0:return b''
                self._condition.wait(remaining)
            # Upstream inventory uses buffer.find(last_frame); identical frames
            # in one read can be counted again. Deliver at most one synthetic
            # frame per read, retaining ordinary partial-read semantics.
            count=min(size,self._frame_sizes[0]) if self._frame_sizes else 0
            raw=bytes(self._rx[:count]);del self._rx[:count]
            if count:
                self._frame_sizes[0]-=count
                if self._frame_sizes[0]==0:self._frame_sizes.popleft()
            return raw

    def flush_input(self):
        if self.backend=='NATION' and self.ser is not None:
            self.ser.reset_input_buffer()
        with self._condition:
            self._rx.clear()
            self._frame_sizes.clear()

    def close(self):
        self._stop_worker()
        if self.ser is not None:
            try:
                if self.zk:self.zk.close()
                else:self.ser.close()
            finally:
                self.ser=self.zk=self.radio=None
                self.backend=None
                with self._condition:self._condition.notify_all()


class AutoReader(NRNReader):
    """Only replace construction/UART and explicit cleanup, not vendor SDK source."""
    def __init__(self,port,baudrate=115200,timeout=0.15,logger=None,*,allow_approximate=False,allow_approximate_rf=False,trace=None,serial_factory=None):
        if not isinstance(timeout,(int,float)) or not 0<timeout<=30:
            raise ValueError('timeout must be in (0,30] seconds')
        if logger is None:
            logger=logging.getLogger('reader_test')
            logger.setLevel(logging.WARNING)
        super().__init__(port,baudrate,timeout=timeout,logger=logger)
        self._inventory_running=False
        self._inventory_thread=None
        self.uart=AutoUART(port,baudrate,timeout=timeout,logger=logger,allow_approximate=allow_approximate,
                           allow_approximate_rf=allow_approximate_rf,trace=trace,serial_factory=serial_factory)

    def close(self):
        if self._inventory_running:
            self.stop_inventory()
        self._inventory_running=False
        if self._inventory_thread and self._inventory_thread is not threading.current_thread():
            self._inventory_thread.join()
        self.uart.close()
