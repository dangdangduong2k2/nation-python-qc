import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

import serial
from serial.tools import list_ports
import win32event
import win32service
import win32serviceutil
import servicemanager

from service_bridge import Bridge, DATA, logger
from qc_uart import QCUART
from zk_nation.vendor.nrn import NRNReader

VERSION='0.2.2-pyqc.2'
NAME='NationPythonQC'
OLD='NationZkComBridge'
ROOT=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'NationPythonQC'
ASSETS=Path(getattr(sys,'_MEIPASS',Path(__file__).parent.parent))/'assets'
CONFIG=DATA/'settings.json'
SC=Path(os.environ.get('WINDIR','C:/Windows'))/'System32/sc.exe'


class QCService(win32serviceutil.ServiceFramework):
    _svc_name_=NAME
    _svc_display_name_='Nation Python QC — RS232 bridge'
    _svc_description_='Nation frame translation to ZK 1-port using Python. Public virtual COM for existing applications.'
    def __init__(self,args):
        super().__init__(args);self.stop_event=threading.Event();self.bridge=None
    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING,waitHint=30000)
        self.stop_event.set()
    def SvcDoRun(self):
        try:
            self.bridge=Bridge(json.loads(CONFIG.read_text(encoding='utf-8')),self.stop_event)
            self.bridge.run()
        except Exception:
            logger().exception('Service failed');raise


def command(args,check=True,timeout=60,cwd=None):
    r=subprocess.run([str(a) for a in args],capture_output=True,text=True,errors='replace',timeout=timeout,cwd=cwd,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    logger().info('COMMAND %s -> %s %s %s',args,r.returncode,r.stdout.strip(),r.stderr.strip())
    if check and r.returncode: raise RuntimeError((r.stdout+'\n'+r.stderr).strip())
    return r


def exists(name):
    try: win32serviceutil.QueryServiceStatus(name);return True
    except Exception: return False


def stop_service(name):
    if exists(name):
        status=win32serviceutil.QueryServiceStatus(name)[1]
        if status!=win32service.SERVICE_STOPPED:
            win32serviceutil.StopService(name)
            win32serviceutil.WaitForServiceStatus(name,win32service.SERVICE_STOPPED,30)


def pair_ports(listing,pair):
    ports={}
    for side in ('A','B'):
        m=re.search(r'(?m)^\s*CNC'+side+str(pair)+r'\s+([^\r\n]+)',listing)
        if not m: raise RuntimeError('Windows has not created the COM pair; restart may be required')
        n=re.search(r'(?:^|,)RealPortName=(COM\d+)(?:,|$)',m[1]) or re.search(r'(?:^|,)PortName=(COM\d+)(?:,|$)',m[1])
        if not n: raise RuntimeError('Windows has not assigned a COM number')
        ports[side]=n[1]
    if ports['A']==ports['B']: raise RuntimeError('COM pair endpoints overlap')
    return ports


def probe(public):
    with serial.Serial(public,115200,timeout=.1,write_timeout=2) as port:
        port.write(NRNReader.build_frame(0x202))
        deadline=time.monotonic()+8;buf=bytearray()
        while time.monotonic()<deadline:
            buf.extend(port.read(max(1,port.in_waiting)))
            if len(buf)>=7:
                size=9+int.from_bytes(buf[5:7],'big')
                if len(buf)>=size:
                    raw=bytes(buf[:size]);p=NRNReader.parse_frame(raw)
                    if p['valid'] and p['category']==2 and p['mid']==2 and p['data']:
                        return raw.hex().upper()
                    raise RuntimeError('Unexpected Nation response through public COM: '+raw.hex())
        raise RuntimeError('No valid Nation response through '+public+'; check reader/baud/cable')


def install(physical,progress):
    if not re.fullmatch(r'COM[1-9][0-9]*',physical): raise ValueError('Invalid physical COM')
    ROOT.mkdir(parents=True,exist_ok=True);DATA.mkdir(parents=True,exist_ok=True)
    progress(5,'Checking ports…')
    old_root=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'NationComPort'
    old_config=old_root/'settings.xml'
    previous=json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else None
    prior_old_start=None;old_running=False;was_new_running=False;created_service=False;previous_service=None
    if previous: config=dict(previous)
    elif old_config.exists() and (old_root/'owner.txt').exists() and (old_root/'owner.txt').read_text().strip()=='NationComPort-package-0.2':
        xml=ET.parse(old_config).getroot()
        config={'public':xml.findtext('NationPort'),'internal':xml.findtext('BridgePort'),'pair':xml.findtext('Pair'),'reused_old_pair':True}
    else:
        config={'public':'COM52','internal':'COM53','pair':None,'reused_old_pair':False}
    for key in ('public','internal'):
        if not re.fullmatch(r'COM[1-9][0-9]*',str(config[key])): raise ValueError('Invalid virtual COM configuration')
    if physical in (config['public'],config['internal']): raise ValueError('Choose the physical reader COM, not the virtual COM')
    config.update(physical=physical,baudrate=115200,version=VERSION)
    config.setdefault('phase',True);config.setdefault('fast_tid',True)
    if config['reused_old_pair'] or previous:
        # Check exclusive access before touching the old service.
        with serial.Serial(config['public'],115200,timeout=.05): pass
    if exists(OLD):
        old_running=win32serviceutil.QueryServiceStatus(OLD)[1]==win32service.SERVICE_RUNNING
        h=win32service.OpenSCManager(None,None,win32service.SC_MANAGER_CONNECT)
        s=win32service.OpenService(h,OLD,win32service.SERVICE_QUERY_CONFIG)
        try: prior_old_start=win32service.QueryServiceConfig(s)[1]
        finally: win32service.CloseServiceHandle(s);win32service.CloseServiceHandle(h)
    if previous and 'old_start' in previous: config['old_start']=previous['old_start']
    else: config['old_start']=prior_old_start
    config['old_running']=previous.get('old_running',old_running) if previous else old_running
    backup=DATA/'before-python.json'
    if not backup.exists(): backup.write_text(json.dumps(config,indent=2),encoding='utf-8')
    if previous: (DATA/'settings.previous.json').write_text(json.dumps(previous,indent=2),encoding='utf-8')
    if exists(NAME):
        h=win32service.OpenSCManager(None,None,win32service.SC_MANAGER_CONNECT)
        s=win32service.OpenService(h,NAME,win32service.SERVICE_QUERY_CONFIG)
        try: previous_service=win32service.QueryServiceConfig(s)
        finally: win32service.CloseServiceHandle(s);win32service.CloseServiceHandle(h)
    try:
        progress(15,'Stopping previous bridge…')
        was_new_running=exists(NAME) and win32serviceutil.QueryServiceStatus(NAME)[1]==win32service.SERVICE_RUNNING
        stop_service(NAME);stop_service(OLD)
        progress(30,'Installing Python runtime…')
        source=Path(sys.executable)
        if not getattr(sys,'frozen',False): raise RuntimeError('Use the built EXE to install')
        digest=hashlib.sha256(source.read_bytes()).hexdigest()[:12]
        target=ROOT/('NationPythonQC-'+digest+'.exe')
        if not target.exists(): shutil.copy2(source,target)
        for name in ('README.md','THIRD_PARTY_NOTICES.md'):
            if (ASSETS/name).exists(): shutil.copy2(ASSETS/name,ROOT/name)
        progress(45,'Preparing compatible COM ports…')
        if not config['reused_old_pair'] and not previous:
            driver=ROOT/'driver/x64'
            shutil.copytree(ASSETS/'driver/x64',driver,dirs_exist_ok=True)
            manifest=json.loads((ASSETS/'driver-sha256.json').read_text())
            for name,expected in manifest.items():
                if hashlib.sha256((driver/name).read_bytes()).hexdigest()!=expected: raise RuntimeError('Driver hash mismatch')
            command([driver/'setupc.exe','--silent','--wait','30','preinstall'],cwd=driver)
            listing=command([driver/'setupc.exe','--silent','list'],cwd=driver).stdout
            used={int(n) for n in re.findall(r'(?m)^\s*CNC[AB](\d+)\s',listing)}
            pair=next(n for n in range(1000000) if n not in used);config['pair']=str(pair)
            (DATA/'pending-pair.json').write_text(json.dumps({'pair':pair,'driver':str(driver)}),encoding='utf-8')
            # COM# uses the standard Ports class that browsers enumerate. Let
            # Windows assign names, then hide only the private endpoint.
            command([driver/'setupc.exe','--silent','--wait','30','install',str(pair),'PortName=COM#,EmuOverrun=yes','PortName=COM#,EmuOverrun=yes,dsr=ropen'],cwd=driver)
            ports=None
            for attempt in range(15):
                try:
                    ports=pair_ports(command([driver/'setupc.exe','--silent','list'],cwd=driver).stdout,pair);break
                except RuntimeError:
                    time.sleep(1)
            if ports is None: raise RuntimeError('Windows did not create serial ports; check driver/restart. No security setting was changed.')
            config['public']=ports['A'];config['internal']=ports['B']
            command([driver/'setupc.exe','--silent','--wait','30','change','CNCB'+str(pair),'PortName='+ports['B']+',HiddenMode=yes'],cwd=driver)
            after=pair_ports(command([driver/'setupc.exe','--silent','list'],cwd=driver).stdout,pair)
            if after!=ports: raise RuntimeError('Virtual COM mapping changed unexpectedly')
        CONFIG.write_text(json.dumps(config,indent=2),encoding='utf-8')
        progress(65,'Registering background service…')
        binpath=f'"{target}" --service'
        if exists(NAME): command([SC,'config',NAME,'binPath=',binpath,'start=','auto'])
        else:
            command([SC,'create',NAME,'binPath=',binpath,'start=','auto','DisplayName=','Nation Python QC — RS232 bridge']);created_service=True
        command([SC,'description',NAME,QCService._svc_description_])
        progress(80,'Starting Python bridge…')
        win32serviceutil.StartService(NAME)
        win32serviceutil.WaitForServiceStatus(NAME,win32service.SERVICE_RUNNING,30)
        progress(90,'Verifying Nation frame through COM…')
        time.sleep(1)
        response=probe(config['public'])
        logger().info('INSTALL PASS: %s ↔ %s Nation power response=%s',config['public'],physical,response)
        if exists(OLD): command([SC,'config',OLD,'start=','demand'])
        progress(100,'Complete')
        return 'Complete. Web: NATION → '+config['public']
    except Exception:
        logger().exception('Install failed; rolling back services')
        stop_service(NAME)
        if created_service: command([SC,'delete',NAME],check=False)
        if previous:
            CONFIG.write_text(json.dumps(previous,indent=2),encoding='utf-8')
        if previous_service:
            command([SC,'config',NAME,'binPath=',previous_service[3],'start=',{2:'auto',3:'demand',4:'disabled'}.get(previous_service[1],'demand')],check=False)
            if was_new_running: win32serviceutil.StartService(NAME)
        if prior_old_start is not None:
            command([SC,'config',OLD,'start=',{2:'auto',3:'demand',4:'disabled'}.get(prior_old_start,'demand')],check=False)
            if old_running and not was_new_running: win32serviceutil.StartService(OLD)
        raise


def uninstall():
    stop_service(NAME)
    if exists(NAME): command([SC,'delete',NAME])
    if CONFIG.exists():
        c=json.loads(CONFIG.read_text(encoding='utf-8'))
        if exists(OLD) and c.get('old_start') is not None:
            command([SC,'config',OLD,'start=',{2:'auto',3:'demand',4:'disabled'}.get(c['old_start'],'demand')])
            if c.get('old_running'): win32serviceutil.StartService(OLD)
    return 'Python QC stopped. Previous bridge restored. COM driver retained.'


def gui(install_port=None,remove=False):
    import tkinter as tk
    from tkinter import ttk,messagebox
    root=tk.Tk();root.title('driver RS232 bridge');root.geometry('420x160');root.resizable(False,False)
    frame=ttk.Frame(root,padding=18);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='driver RS232 bridge',font=('Segoe UI',13)).pack(anchor='w')
    ports=[p.device for p in list_ports.comports() if 'Nation' not in p.description and 'Bluetooth' not in p.description]
    selection=tk.StringVar(value='COM50' if 'COM50' in ports else ports[0] if ports else '')
    combo=ttk.Combobox(frame,textvariable=selection,values=ports,state='readonly');combo.pack(fill='x',pady=8)
    bar=ttk.Progressbar(frame,maximum=100);phase=tk.StringVar(value='')
    ttk.Label(frame,textvariable=phase).pack(anchor='w')
    buttons=ttk.Frame(frame);buttons.pack(fill='x')
    events=queue.Queue();working=False
    def go():
        nonlocal working
        physical=install_port or selection.get()
        if not physical and not remove: messagebox.showerror(root.title(),'Select physical COM');return
        if not ctypes.windll.shell32.IsUserAnAdmin():
            args='--uninstall' if remove else '--install '+physical
            result=ctypes.windll.shell32.ShellExecuteW(None,'runas',sys.executable,args,None,1)
            if result<=32: messagebox.showerror(root.title(),'Administrator approval was not granted');return
            root.destroy();return
        working=True;setup.config(state='disabled');cancel.config(state='disabled');combo.config(state='disabled')
        root.geometry('420x190');bar.pack(fill='x',before=buttons,pady=6)
        def worker():
            try: events.put(('done',uninstall() if remove else install(physical,lambda n,s:events.put(('progress',(n,s))))))
            except Exception as e: events.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    setup=ttk.Button(buttons,text='Setup',command=go);setup.pack(side='left')
    cancel=ttk.Button(buttons,text='Cancel',command=root.destroy);cancel.pack(side='right')
    def poll():
        nonlocal working
        try:
            while True:
                kind,value=events.get_nowait()
                if kind=='progress': bar['value']=value[0];phase.set(value[1])
                else:
                    working=False
                    if kind=='done': messagebox.showinfo(root.title(),value);root.destroy();return
                    messagebox.showerror(root.title(),value+'\nLog: '+str(DATA/'bridge.log'));setup.config(state='normal');cancel.config(state='normal')
        except queue.Empty: pass
        root.after(100,poll)
    root.protocol('WM_DELETE_WINDOW',lambda:None if working else root.destroy())
    root.after(100,poll)
    if install_port or remove: root.after(200,go)
    root.mainloop()


def self_test():
    from zk_nation.core.simulation import MemorySerial
    class Fake(MemorySerial):
        def reset_input_buffer(self): self.buffer.clear()
    uart=QCUART('simulation',serial_factory=lambda **kw:Fake(1),allow_approximate=True)
    try:
        uart.open();uart.send(NRNReader.build_frame(0x202))
        p=NRNReader.parse_frame(uart.receive(256))
        assert p['valid'] and p['category']==2 and p['mid']==2
        manifest=json.loads((ASSETS/'driver-sha256.json').read_text())
        for name,digest in manifest.items():
            assert hashlib.sha256((ASSETS/'driver/x64'/name).read_bytes()).hexdigest()==digest,name
        assert (ASSETS/'licenses/pyserial-LICENSE.txt').is_file()
        from zk_nation.core.reader import parse_inventory
        from zk_nation.core.protocol import Response
        tag=parse_inventory(Response(0,1,3,bytes.fromhex('0101D8E280F30200000000C461A988E280F30220000000C461A98842003900300E1019')),1)[0]
        assert tag['tid']=='E280F30220000000C461A988' and tag['phase_end_raw']==48 and tag['frequency_khz']==921625
        result={'version':VERSION,'frozen':bool(getattr(sys,'frozen',False)),'python':sys.version,'result':'PASS','power_response':p['data'].hex(),'assets':str(ASSETS),'driver_assets_verified':len(manifest),'phase_fastid_parser_verified':True}
        target=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'NationPythonQC-selftest.json'
        target.write_text(json.dumps(result,indent=2),encoding='utf-8')
    finally: uart.close()


def main():
    if '--service' in sys.argv:
        servicemanager.Initialize();servicemanager.PrepareToHostSingle(QCService);servicemanager.StartServiceCtrlDispatcher()
    elif '--self-test' in sys.argv: self_test()
    elif '--install' in sys.argv: gui(install_port=sys.argv[sys.argv.index('--install')+1])
    elif '--uninstall' in sys.argv: gui(remove=True)
    else: gui()


if __name__=='__main__': main()
