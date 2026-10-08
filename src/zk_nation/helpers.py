"""Code test rieng: tu nhan Nation/ZK, giu SDK/parser/frame Nation cua Binh."""
from collections import Counter
import json
import math
import threading
import time


def show(data):
    print(json.dumps(data,ensure_ascii=False,indent=2,default=str))


def call(reader,name,*args,**kwargs):
    """SDK goc co ham tra False/{} thay vi throw. Khong coi do la thanh cong."""
    value=getattr(reader,name)(*args,**kwargs)
    if reader.uart.last_error or value is False or value is None or value=={} or (isinstance(value,dict) and value.get('mode')=='error'):
        raise RuntimeError(json.dumps({'api':name,'result':value,'error':reader.uart.last_error},ensure_ascii=False,default=str))
    return value


def get_parameters(reader):
    result={'detected':reader.uart.backend,'physical_info':reader.uart.device_info}
    for name in ('Query_Reader_Information','query_rfid_ability','query_reader_power',
                 'query_rf_band','query_working_frequency','query_baseband_profile'):
        try:result[name]=call(reader,name)
        except Exception as error:result[name]={'error':str(error)}
    result['mapping_notes']=reader.uart.mapping_notes
    return result


def inventory(reader,seconds=5):
    if not math.isfinite(seconds) or not 0<seconds<=3600:raise ValueError('seconds must be in (0,3600]')
    counts,latest=Counter(),{}
    lock=threading.Lock()
    callback_errors=[]
    def on_tag(tag):
        try:
            epc=tag['epc']
            with lock:
                if epc not in counts and len(counts)>=10000:
                    raise RuntimeError('10000 EPC limit reached; stopping test')
                counts[epc]+=1
                latest[epc]=tag
        except Exception as error:
            callback_errors.append(error)
    call(reader,'start_inventory_with_mode',[1],callback=on_tag)
    end=time.monotonic()+seconds
    try:
        while time.monotonic()<end and reader.is_inventory_running() and not callback_errors:
            time.sleep(min(0.1,max(0,end-time.monotonic())))
    except KeyboardInterrupt:
        print('Stopping...')
    finally:
        stopped=reader.stop_inventory()
    if callback_errors:raise callback_errors[0]
    if reader.uart.last_inventory_error:raise RuntimeError(str(reader.uart.last_inventory_error))
    if not stopped:raise RuntimeError('STOP was not acknowledged; close and check the reader before continuing')
    with lock:
        return {'backend':reader.uart.backend,'reports':sum(counts.values()),'unique':len(counts),
                'tags':[{**latest[epc],'count':count} for epc,count in counts.items()]}


def set_power(reader,dbm):
    call(reader,'configure_reader_power',{1:dbm},persistence=False)
    actual=call(reader,'query_reader_power')
    if actual.get(1)!=dbm:raise RuntimeError('Power readback mismatch; do not automatically retry')
    return actual


def set_baseband(reader,speed,q,session,target=0):
    call(reader,'configure_baseband',speed,q,session,target)
    actual=call(reader,'query_baseband_profile')
    if actual.get('q_value')!=q or actual.get('session')!=session or actual.get('inventory_flag')!=target:
        raise RuntimeError('Q/Session readback mismatch; settings may be partially changed')
    # ZK->Nation can canonicalize speed; raw ZK and candidates are kept separately.
    return {'actual':actual,'mapping':reader.uart.mapping_notes.get('baseband')}


def write_epc_same_length(reader,current,new):
    current=current.replace(' ','').upper();new=new.replace(' ','').upper()
    if not current or len(current)!=len(new) or len(new)%4 or len(new)>60:
        raise ValueError('Use two EPCs of equal length, whole words, at most 30 bytes')
    bytes.fromhex(current);bytes.fromhex(new)
    # Original SDK's write_epc_tag_auto builds an ambiguous mask at word1.
    # This test deliberately uses the ordinary selected write at EPC word2.
    result=call(reader,'write_epc_tag',new,antenna_id=1,match_epc_hex=current,start_word=2,timeout=3)
    result['verified']=False
    if not result.get('success'):raise RuntimeError(str(result))
    return result
