"""Nation-style function facade over direct ZK serial; no Nation DLL or COM bridge.

Mappings match rs232-bridge RC9. Many Nation profiles share a ZK preset, so Get
returns candidates instead of inventing a unique Nation mode. No host state file.
"""
from .reader import Reader, hex_bytes, selector_bytes
from .protocol import integer
from .errors import UnsupportedError, ValidationError, VerificationError
from .nation_errors import nation_errors, nation_error_context


# Nextwaves nrn.py IDs, NOT GReaderTool v0.39. Speed4 is Miller4,
# so ZK123 (Miller2) is wrong here. Every preset differs in at least Tari.
PROFILES = {0: 205, 1: 146, 2: 141, 3: 203, 4: 141, 255: 146}
MATCHING_PROFILES = frozenset()
# Nation band -> ZK band, first ZK channel, number of channels
REGIONS = {0: (1, 2, 16), 1: (8, 2, 16), 3: (2, 0, 50), 4: (9, 0, 4),
           5: (31, 0, 4), 13: (9, 1, 3), 15: (1, 0, 20)}


class NationReader(Reader):
    """Import into the customer's process; inherited raw methods use ZK IDs.

    get/set_frequency and get/set_baseband use Nation IDs. Set operations can
    partially succeed on a transport error: never retry writes automatically.
    """

    @nation_errors("get_reader_info")
    def get_reader_info(self):
        return self.info()

    @nation_errors("get_power")
    def get_power(self):
        return self.power()

    @nation_errors("set_power")
    def set_power(self, dbm, *, antenna=None, persist=False):
        integer(dbm, 0, 30, "dbm")
        if type(persist) is not bool:
            raise ValueError("persist must be boolean")
        if antenna is None:
            return self.power(dbm=dbm, persist=persist)
        integer(antenna, 1, self.antennas, "antenna")
        with self.transport.lock:
            values = self.power()["powers_dbm"]
            values[antenna - 1] = dbm
            return self.power(powers_dbm=values, persist=persist)

    @nation_errors("get_frequency")
    def get_frequency(self):
        actual = self.region()
        candidates = []
        for nation, (zk, offset, count) in REGIONS.items():
            if (actual["band"] == zk and offset <= actual["min_channel"]
                    <= actual["max_channel"] < offset + count):
                candidates.append({"nation_band": nation,
                                   "channels": list(range(actual["min_channel"] - offset,
                                                          actual["max_channel"] - offset + 1))})
        return {"zk": actual, "nation_candidates": candidates,
                "mapped": bool(candidates), "simulated": self.simulated}

    def set_frequency(self, nation_band, channels=None, *, persist=False):
        # Full-band selection corresponds to Nation SetRegion (0x203);
        # explicit channels correspond to SetFrequency (0x205).
        operation = "set_region" if channels is None else "set_frequency"
        with nation_error_context(operation):
            return self._set_frequency(nation_band, channels, persist=persist)

    def _set_frequency(self, nation_band, channels=None, *, persist=False):
        integer(nation_band, 0, 255, "nation_band")
        if nation_band not in REGIONS:
            raise ValidationError("nation_band", "Nation band has no verified ZK mapping")
        band, offset, count = REGIONS[nation_band]
        if channels is None:
            selected = list(range(count))
        else:
            if not isinstance(channels, (list, tuple)) or not channels:
                raise ValidationError("channels", "channels must be a nonempty list or None for full range")
            selected = sorted(integer(c, 0, count - 1, "channel") for c in channels)
            if any(b != a + 1 for a, b in zip(selected, selected[1:])):
                raise ValidationError("channel", "Channels must be unique and contiguous")
        with self.transport.lock:
            self.region(band, offset + selected[0], offset + selected[-1], persist)
            return self.get_frequency()

    @nation_errors("get_baseband")
    def get_baseband(self):
        with self.transport.lock:
            profile, query = self.profile(), self.query()
        candidates = [n for n, z in PROFILES.items()
                      if profile["format"] == "extended" and z == profile["profile_id"]]
        return {"zk_profile": profile, "epc_speed_candidates": candidates,
                "matching_rf_candidates": [n for n in candidates if n in MATCHING_PROFILES],
                "q": query["q"], "session": query["session"], "simulated": self.simulated}

    @nation_errors("set_baseband")
    def set_baseband(self, *, epc_speed=None, q=None, session=None,
                     allow_approximate=False, persist=False):
        if type(allow_approximate) is not bool or type(persist) is not bool:
            raise ValueError("allow_approximate/persist must be boolean")
        if epc_speed is None and q is None and session is None:
            raise ValueError("Provide epc_speed, q or session")
        if epc_speed is not None:
            integer(epc_speed, 0, 255, "epc_speed")
            if epc_speed not in PROFILES:
                raise ValidationError("epc_speed", "Unsupported Nation EPC speed")
            if epc_speed not in MATCHING_PROFILES and not allow_approximate:
                raise ValueError("Approximate RF preset requires allow_approximate=True")
        if q is not None:
            integer(q, 0, 15, "q")
        if session is not None:
            integer(session, 0, 3, "Nation session")
        with self.transport.lock:
            self.get_baseband()  # Read-only preflight before any mutation.
            if epc_speed is not None:
                self.profile(PROFILES[epc_speed], persist=persist, format="extended")
            if q is not None or session is not None:
                self.query(q=q, session=session, persist=persist)
            return self.get_baseband()

    @nation_errors("read_tag")
    def read_tag(self, **kwargs):
        return self.read(**kwargs)

    @nation_errors("write_tag")
    def write_tag(self, **kwargs):
        return self.write(**kwargs)

    def write_tag_verified(self, *, antenna, bank, word_address, hex, selector,
                           password="00000000"):
        """Explicit write ONCE then readback, using a stable TID selector.

        Does not retry, rollback, change EPC PC/length, or guarantee selector
        uniqueness. Initial write errors retain their original exception.
        """
        selector_bytes(selector)
        if selector["bank"] != 2:
            raise ValidationError("selector", "Verified write requires a stable TID selector")
        data = hex_bytes(hex, "hex", 2, 64, words=True)
        with self.transport.lock:
            result = self.write_tag(antenna=antenna, bank=bank, word_address=word_address,
                                    hex=hex, selector=selector, password=password)
            try:
                actual = self.read_tag(antenna=antenna, bank=bank, word_address=word_address,
                                       words=len(data)//2, selector=selector, password=password)
            except Exception as error:
                raise VerificationError(cause=error) from error
            if actual["hex"] != data.hex().upper():
                raise VerificationError(actual_hex=actual["hex"])
            return dict(result, verified=True)

    def _inventory_query(self):
        with nation_error_context("get_baseband"):
            return self.query()

    def inventory_once(self, *, q=None, session=None, **kwargs):
        """Use real CFG9 for omitted Q/Session; all duplicates remain present."""
        with self.transport.lock:
            if q is None or session is None:
                query = self._inventory_query()
                q = query["q"] if q is None else q
                session = query["session"] if session is None else session
            with nation_error_context("inventory"):
                if session == 255:
                    raise UnsupportedError("CFG9 Auto Session is not supported by ordinary inventory; pass session=0..3")
                return self.inventory(q=q, session=session, **kwargs)

    def iter_inventory(self, stop_event, **kwargs):
        """Yield finite batches until stop_event is set. No thread/queue or auto retry.

        The current serial transaction completes before stopping. Consumer speed
        controls the next round; no background queue can silently drop reports.
        """
        if stop_event.is_set():
            return
        # Snapshot native Q/Session once per Start; no configuration polling
        # between RF rounds. A new Start reads the device again.
        if kwargs.get("q") is None or kwargs.get("session") is None:
            query = self._inventory_query()
            for name in ("q", "session"):
                if kwargs.get(name) is None:
                    kwargs[name] = query[name]
        while not stop_event.is_set():
            yield self.inventory_once(**kwargs)

    def run_inventory(self, on_batch, stop_event, **kwargs):
        """Blocking callback loop, intended for an application-owned worker thread."""
        if not callable(on_batch):
            raise TypeError("on_batch must be callable")
        for batch in self.iter_inventory(stop_event, **kwargs):
            on_batch(batch)
