"""Device band/channel tables from Ex10 manual v2.25, section 8.4.2.

These are firmware channel definitions, not a claim of regulatory permission.
All arithmetic uses integer kHz to avoid rounded channel selection.
"""
from .protocol import integer


def channels(base, step, count):
    return tuple(base + step*i for i in range(count))


BANDS = {
    0: ("All band", channels(840000, 2000, 61)),
    1: ("Chinese band2", channels(920125, 250, 20)),
    2: ("US band", channels(902750, 500, 50)),
    3: ("Korean band", channels(917100, 200, 32)),
    4: ("EU band", channels(865100, 200, 15)),
    6: ("Ukraine band", channels(868000, 100, 7)),
    8: ("Chinese band1", channels(840125, 250, 20)),
    9: ("EU3 band", channels(865700, 600, 4)),
    12: ("US band3", channels(902000, 500, 53)),
    16: ("HK band", channels(920250, 500, 10)),
    17: ("Taiwan band", channels(920750, 500, 14)),
    18: ("ETSI UPPER band", channels(916300, 1200, 3)),
    19: ("Malaysia band", channels(919250, 500, 8)),
    21: ("Brazil band", channels(902750, 500, 10) + channels(915250, 500, 25)),
    22: ("Thailand band", channels(920250, 500, 10)),
    23: ("Singapore band", channels(920250, 500, 10)),
    24: ("Australia band", channels(920250, 500, 10)),
    25: ("India band", channels(865100, 600, 4)),
    26: ("Uruguay band", channels(916250, 500, 23)),
    27: ("Vietnam band", channels(918750, 500, 8)),
    28: ("Israel band", (916250,)),
    29: ("Indonesia band", (917250, 920250, 920750, 921250)),
    30: ("New Zealand band", channels(922250, 500, 10)),
    31: ("Japan2 band", channels(916800, 1200, 4)),
    32: ("Peru band", channels(916250, 500, 23)),
    33: ("Russia band", channels(916200, 1200, 4)),
    34: ("South Africa band", channels(915600, 200, 17)),
    35: ("Philippines band", channels(918250, 500, 4)),
}


def validate_region(band, min_channel, max_channel):
    integer(band, 0, 255, "band")
    if band not in BANDS:
        raise ValueError("Band is not in the verified channel table")
    last = len(BANDS[band][1])-1
    integer(min_channel, 0, last, "min_channel")
    integer(max_channel, min_channel, last, "max_channel")


def region_description(band, minimum, maximum):
    entry = BANDS.get(band)
    valid = entry is not None and minimum <= maximum < len(entry[1])
    return {"band": band, "min_channel": minimum, "max_channel": maximum,
            "band_name": entry[0] if entry else None,
            "frequencies_khz": list(entry[1][minimum:maximum+1]) if valid else None,
            "table_known": valid}
