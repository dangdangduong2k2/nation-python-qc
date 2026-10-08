# Third-party notices

This independent QC package is not a vendor release or endorsement.

- `src/zk_nation/vendor/nrn.py`: unchanged Nextwaves Industries Nation Python SDK, upstream commit `d416b0d3bd5a103cf6b1833b13f0c88e9dfbe24b`, https://github.com/Nextwaves-Industries/nextwaves-sdk, path `sdk/nation/python/nrn.py`. SHA256 `f018a7e6926a6681834b7860284ed43f3f59bbc3df09f0ddca91feb0074c6c05`. MIT license retained in `src/zk_nation/vendor/LICENSE` and `assets/licenses/Nextwaves-MIT.txt`.
- Supporting `src/zk_nation/` integration copied from https://github.com/dangdangduong2k2/zk-nation-python-adapter at commit `7eeb85f330eb71bc667f51e70ac0b4d461f9f1d8`. QC version `.2` extends inventory parsing and Q/Session handling for phase/FastID; the vendor `nrn.py` remains unchanged. Its protocol implementation references the supplied Ex10 SDK manual; vendor DLLs and SDK documents are not redistributed. Model identifiers reference Ex10 SDK V6.8 ReaderType.ini; that full INI is not bundled.
- Python runtime: bundled by PyInstaller; license in `assets/licenses/Python-LICENSE.txt`.
- pySerial 3.5: BSD license in `assets/licenses/pyserial-LICENSE.txt`; https://github.com/pyserial/pyserial.
- pywin32 312: bundled license notices in `assets/licenses/pywin32-*`; https://github.com/mhammond/pywin32.
- PyInstaller 6.20.0: bootloader redistribution terms and exceptions in `assets/licenses/pyinstaller-COPYING.txt`; https://github.com/pyinstaller/pyinstaller.

## Serial driver

The Windows EXE includes the signed com0com x64 driver/setup used by the existing bridge, with signatures preserved. Catalog CAB SHA256: `C14225D86E4AD4A8414F7FB44F0014D7A8A1FD1993EC76CF79E5B2E7FE47DB51`. Individual driver file hashes are in `assets/driver-sha256.json`.

com0com GPLv2 license and upstream source archive are provided in `assets/LICENSE-com0com.txt` and `assets/com0com-source.zip`. The driver is a separate serial device component. No security settings are changed by this installer.

No Node.js, JavaScript application engine or embedded browser is included in this EXE. Independent integration source retains the root MIT LICENSE; third-party components retain their respective licenses.
