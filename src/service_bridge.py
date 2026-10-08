import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import threading
import time

import serial
from qc_uart import QCUART

DATA = Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')) / 'NationPythonQC'


def logger():
    DATA.mkdir(parents=True,exist_ok=True)
    log=logging.getLogger('python-qc');log.setLevel(logging.INFO)
    if not log.handlers:
        handler=RotatingFileHandler(DATA/'bridge.log',maxBytes=10_000_000,backupCount=4,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));log.addHandler(handler)
    return log


class FrameAssembler:
    def __init__(self): self.buffer=bytearray()
    def feed(self,raw):
        self.buffer.extend(raw);out=[]
        if len(self.buffer)>65544: raise ValueError('Nation command buffer overflow')
        while len(self.buffer)>=7:
            if self.buffer[0]!=0x5a: raise ValueError('Expected Nation 0x5A header')
            extra=int(bool(self.buffer[3]&32))
            if len(self.buffer)<7+extra: break
            size=9+extra+int.from_bytes(self.buffer[5+extra:7+extra],'big')
            if size>65544: raise ValueError('Invalid Nation frame size')
            if len(self.buffer)<size: break
            out.append(bytes(self.buffer[:size]));del self.buffer[:size]
        return out


class TracedSerial(serial.Serial):
    def __init__(self,trace,**kwargs):
        self.trace=trace
        super().__init__(**kwargs)
    def write(self,raw):
        self.trace('WIRE_TX',raw)
        return super().write(raw)
    def read(self,size=1):
        raw=super().read(size)
        if raw: self.trace('WIRE_RX_CHUNK',raw)
        return raw


class Bridge:
    def __init__(self,config,stop=None):
        self.config=config;self.stop=stop or threading.Event();self.log=logger();self.uart=None
        self.receive_stop=threading.Event();self.reader=None;self.failure=None

    def trace(self,direction,raw): self.log.info('%s %s',direction,raw.hex().upper())

    def disconnect(self):
        self.receive_stop.set()
        if self.uart:
            self.uart.active=False
            self.uart.close()
        if self.reader: self.reader.join(timeout=4)
        self.reader=None;self.uart=None

    def pump(self,internal,uart):
        try:
            while not self.receive_stop.is_set():
                raw=uart.receive(65536)
                if raw and not self.receive_stop.is_set():
                    if internal.write(raw)!=len(raw): raise IOError('Partial virtual COM write; outcome unknown')
        except Exception as e:
            if not self.receive_stop.is_set(): self.failure=e

    def run(self):
        c=self.config
        self.log.info('Python QC starting: %s ↔ %s; firmware probing only when web opens public COM',c['public'],c['physical'])
        internal=None;faulted=False;assembly=FrameAssembler()
        try:
            internal=serial.Serial(c['internal'],c.get('baudrate',115200),timeout=.05,write_timeout=2)
            self.log.info('READY public=%s internal=%s physical=%s',c['public'],c['internal'],c['physical'])
            while not self.stop.is_set():
                online=bool(internal.dsr) # com0com internal dsr=ropen: peer is actually open.
                if not online:
                    if self.uart:
                        self.disconnect();self.log.info('Client disconnected; physical COM released')
                    faulted=False;self.failure=None;assembly=FrameAssembler();internal.reset_input_buffer()
                    self.stop.wait(.05);continue
                if faulted:
                    self.stop.wait(.1);continue # No automatic replay/reconnect while the client remains open.
                try:
                    if self.uart is None:
                        self.receive_stop.clear();self.failure=None
                        self.uart=QCUART(c['physical'],c.get('baudrate',115200),timeout=.1,logger=self.log,trace=self.trace,allow_approximate=True,
                            phase=c.get('phase',True),fast_tid=c.get('fast_tid',True),
                            serial_factory=lambda **kw:TracedSerial(trace=self.trace,**kw))
                        self.uart.open();self.log.info('DETECTED %s %s',self.uart.backend,self.uart.device_info)
                        self.reader=threading.Thread(target=self.pump,args=(internal,self.uart),daemon=True);self.reader.start()
                    if self.failure: raise self.failure
                    if self.uart.zk and self.uart.zk.transport.failed: raise IOError('ZK transport desynchronized; disconnect/reconnect web')
                    raw=internal.read(min(max(internal.in_waiting,1),65536))
                    if raw:
                        if self.uart.backend=='NATION': self.uart.send(raw)
                        else:
                            for frame in assembly.feed(raw): self.uart.send(frame)
                except Exception as e:
                    self.log.exception('Connection fault: %s. Disconnect/reconnect web; no write replay.',e)
                    self.disconnect();faulted=True
        finally:
            self.disconnect()
            if internal: internal.close()
            self.log.info('Python QC stopped')
