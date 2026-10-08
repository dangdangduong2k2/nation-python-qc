"""Nextwaves Nation menu -> documented Ex10 channels; differences are explicit."""
from .core.errors import UnsupportedError, ValidationError

# ZK region and inclusive channel bounds, from Ex10 manual V2.25 section 8.4.2.
BANDS = {
    0: {'name':'CN 920-925 MHz', 'legs':[(1,2,17)]},
    1: {'name':'CN 840-845 MHz', 'legs':[(8,2,17)]},
    2: {'name':'CN 840-845 + 920-925 MHz', 'legs':[(8,2,17),(1,2,17)],
        'note':'Host alternates CN ranges between inventory rounds; session only.'},
    3: {'name':'FCC 902-928 MHz', 'legs':[(2,0,49)]},
    4: {'name':'ETSI 866-868 MHz', 'legs':[(9,0,3)]},
    5: {'name':'JP 916.8-920.4 MHz', 'legs':[(31,0,3)]},
    6: {'name':'TW 922.25-927.75 MHz', 'legs':[(17,3,13)], 'approximate':True,
        'note':'ZK ends at 927.25 MHz; 927.75 MHz is unavailable.'},
    7: {'name':'ID 923.125-925.125 MHz', 'legs':[(1,12,19)], 'approximate':True,
        'note':'ZK subset 923.125-924.875 MHz; 925.125 MHz is unavailable.'},
    8: {'name':'RUS 866.6-867.4 MHz', 'legs':[(4,8,11)], 'approximate':True,
        'note':'ZK channels 866.7/866.9/867.1/867.3 MHz, not Nation channel centers.'},
}
SPEEDS = {
    0: {'name':'Tari=25us, FM0, LHF=40KHz','zk':205,'actual':'Tari=20us, FM0, BLF=50KHz'},
    1: {'name':'Tari=25us, Miller4, LHF=250KHz','zk':146,'actual':'Tari=20us, Miller4, BLF=250KHz'},
    2: {'name':'Tari=25us, Miller4, LHF=300KHz','zk':141,'actual':'Tari=20us, Miller4, BLF=320KHz'},
    3: {'name':'Tari=6.25us, FM0, LHF=400KHz','zk':203,'actual':'Tari=12.5us, FM0, BLF=426KHz'},
    4: {'name':'Tari=25us, Miller4, LHF=320KHz','zk':141,'actual':'Tari=20us, Miller4, BLF=320KHz'},
    255: {'name':'Reader automatic setup','zk':146,'actual':'Fixed ZK146; NOT automatic RF optimization'},
}


class RadioMapper:
    def __init__(self, reader):
        self.reader = reader
        self.composite = False
        self.active_leg = None
        self.next_leg = 0

    @staticmethod
    def key(actual):
        return actual['band'],actual['min_channel'],actual['max_channel']

    def _set_leg(self, leg):
        actual = self.reader.region(*leg,persist=False)
        self.active_leg = self.key(actual)
        return actual

    def set(self, band, *, allow_approximate=False):
        if type(band) is not int or band not in BANDS:
            raise ValidationError('nation_band','Nation RF band must be 0..8')
        recipe = BANDS[band]
        if recipe.get('approximate') and not allow_approximate:
            raise UnsupportedError(recipe['note']+' Enable approximate RF mapping explicitly.')
        self.composite = False
        self.next_leg = 0
        # Verify both legs now. Errors indicate possibly partial configuration.
        for leg in recipe['legs']:self._set_leg(leg)
        if len(recipe['legs'])>1:
            self._set_leg(recipe['legs'][0])
            self.composite = True
        return self.get()

    def get(self):
        actual = self.reader.region()
        key = self.key(actual)
        if self.composite:
            if key != self.active_leg:
                self.composite = False
                raise UnsupportedError('Physical region changed; composite session invalidated')
            band = 2
        else:
            exact = [band for band,recipe in BANDS.items()
                     if len(recipe['legs'])==1 and key == recipe['legs'][0]]
            if len(exact)==1:band=exact[0]
            else:
                subset = [band for band,recipe in BANDS.items() if band in (0,1,3,4,5)
                          and key[0]==recipe['legs'][0][0]
                          and recipe['legs'][0][1]<=key[1]<=key[2]<=recipe['legs'][0][2]]
                if len(subset)!=1:raise UnsupportedError('No verified Nation mapping for physical region')
                band=subset[0]
        recipe=BANDS[band]
        return {'nation_band':band,'band_name':recipe['name'],'physical':actual,
                'approximate':recipe.get('approximate',False),
                'mode':'host_alternating' if band==2 else 'hardware',
                'note':recipe.get('note',''), 'legs':[list(leg) for leg in recipe['legs']]}

    def before_round(self):
        if not self.composite:return
        leg = BANDS[2]['legs'][self.next_leg]
        if self.active_leg != leg:self._set_leg(leg)
        self.next_leg = (self.next_leg+1)%2
