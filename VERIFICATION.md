# QC evidence — 2026-10-08

## Update: 0.2.2-pyqc.2 — phase / FastID

21 automated tests PASS (13 previous + 8 extension tests). Frozen EXE self-test PASS, including a combined FastID/phase record captured from COM50 and all 12 driver asset hashes.

`phase-fastid-results.json`: direct COM50 hardware PASS. Three approximately 3-second scans: legacy 58 generated reports / 58 separate TID reads; phase 58 generated reports / 58 separate TID reads; phase+FastID 142 generated reports / **0 separate TID reads**, all 142 reports with phase and FastID TID. Counts consumed by the harness are slightly lower because Stop discards pending buffered reports. These short runs are not controlled throughput benchmarks.

CFG9 phase RAM Set/Get and partial Nation Set Q preserving the phase bit PASS; original CFG9 Q4/Session0/phase off restored. No tag/RF/power writes in this feature test. Raw direct probe includes both phase-only and combined EPC/TID/phase records. Tests parse actual Nation frames with the unchanged `nrn.py` parser. Phase is reduced to the demo's modulo180 display convention and quantized to Nation PID09; see `PHASE_FASTID.md`.

Final installed service PASS: `phase-fastid-service-results.json`, COM52 → Python EXE → COM50, 132 reports consumed during the timed scan plus 5 notifications received before Stop ACK. Service logged 137 phase reports, 137 native FastID TIDs, 0 separate TID read commands. Stop ACK/end and physical COM release verified. Installed/delivery EXE SHA256: `5c250758a090aa80b292047724c93289c04b432cc7e2f2c07ac3b1aac6df1bd3`.

`webserial-parser-results.json`: original, unchanged Nextwaves Web Serial JS SDK parsed an actual service frame and exposed EPC/TID/phase/frequency. The live website UI was not driven again in this feature run; UI rendering remains for the user's QC. No JavaScript runtime was added to the Python EXE.

An initial service test harness incorrectly expected Stop ACK before already-in-flight tag notifications; the harness was corrected to drain those valid notifications first. This was a test harness error, not a service protocol error.

Windows x64, Python 3.14.3, ZK firmware 2.8 reader type 113 / 1 antenna, 115200 baud.

## Automated verification

13 tests in `test_qc.py`: frame fragmentation/coalescing, first Get before Set, partial baseband TLVs, speed/Q/Session/Flag, RF bands and contiguous channels, power, filter, inventory/TID/User/end frames, selected write exactly once, contextual write errors, native Nation byte passthrough (simulation), rejecting 4-antenna hardware, COM pair parsing, and refusing a busy public COM before stopping services.

The frozen EXE is tested from a separate working directory using `--self-test`, including bundled asset hashes. Result: `frozen-selftest.json`. Final distribution hashes: `SHA256SUMS.txt`.

## Physical hardware

`hardware-results.json`: direct COM50, all executed cases PASS; 108 tag reports in 5 seconds continuous inventory. `service-results.json`: actual COM52 → installed Python service → COM50, all executed cases PASS; 107 reports in 5 seconds. These short measurements are observations, not throughput guarantees.

Both runs verified first Get; power Set/Get; RF bands 0/1/3/4/5; channel subset; speeds 0/1/2/3/4/255; Q, Session, Flag; single/continuous inventory; EPC/PC/TID/User; Stop; selected User word 0 write/readback. Test word A55A was restored to 0000 and read back. Original power, band, frequency and baseband settings were restored. See JSON records for actual frames and values.

No GPO toggling, lock/kill, Reserved/TID writes or EPC-length changes were performed.

## Installed service and live website

`NationPythonQC` is running with automatic startup; the old `NationZkComBridge` is retained, stopped, and set to manual. Public COM52, internal COM53, physical COM50. Initial installed EXE SHA256: `7cbbe99174f14423b955a9f2633faac570da074279ede1e861e4027bbe4d5f79`. The final delivery rebuild includes packaging, license and fresh-machine COM setup refinements; the active QC session was left on the verified installed binary.

The unchanged https://app.nextwaves.com/reader, model NATION, connected through COM52 and returned increasing counts, real EPC/TID, Get Band and Get Baseband responses, and Stop ACK/end. Screenshot: `web-qc-proof.png`. A fresh scan after clearing cached rows displayed TID correctly.

The website may display default RSSI −70 dBm / frequency 920 MHz when those fields are absent. These UI defaults are not reader measurements; the bridge does not synthesize them.

## Scope and remaining verification

- Fresh Windows driver installation, reboot/autostart, multi-hour endurance and rollback have not been exercised on a clean machine.
- Native Nation passthrough is covered by simulation; it was not re-tested on physical Nation in this service run.
- ZK 4 antennas is deliberately rejected in this QC build. Use the retained older bridge for that hardware after uninstalling this service.
- Not all Nation SDK commands or device error codes are supported. See README for command scope and web-call limitations.

## Source provenance

Original Nextwaves `nrn.py`, unchanged, commit `d416b0d3bd5a103cf6b1833b13f0c88e9dfbe24b`, SHA256 `f018a7e6926a6681834b7860284ed43f3f59bbc3df09f0ddca91feb0074c6c05`.

Supporting Python integration copied from `zk-nation-python-adapter` commit `7eeb85f330eb71bc667f51e70ac0b4d461f9f1d8`. Current frame conversion is `src/qc_uart.py`, aligned with Web Serial adapter 0.2.2. Runtime is Python, without Node.js or an embedded browser.
