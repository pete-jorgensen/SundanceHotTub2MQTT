import asyncio
import errno
import json
import logging
import logging.handlers
import os
import time
import warnings
import queue
import socket
from socket import error as SocketError
from datetime import date, datetime, timedelta
try:
    from balboa import *
except:
    from .balboa import *
try:
    import panel_menu
except ImportError:
    from . import panel_menu

#Common to all known Balboa Products
CLIENT_CLEAR_TO_SEND = 0x00
CHANNEL_ASSIGNMENT_REQ = 0x01
CHANNEL_ASSIGNMENT_RESPONCE = 0x02
CHANNEL_ASSIGNMENT_ACK = 0x03
EXISTING_CLIENT_REQ = 0x04
EXISTING_CLIENT_RESPONCE = 0x05
CLEAR_TO_SEND = 0x06
NOTHING_TO_SEND =  0x07

#These were found on a Sundance 780 series
STATUS_UPDATE = 0xC4
LIGHTS_UPDATE = 0xCA
CC_REQ = 0xCC

# Jacuzzi support (message types 0x16 / 0x17 / 0x23) has been REMOVED.
# Those frames were routed into the Sundance parsers, which apply the xormsg fold - but a
# 0x16 payload is PLAINTEXT (12 zero bytes in 32, against none in a Sundance payload), so
# folding it produced noise and silently mis-decoded every Jacuzzi tub. Rather than ship a
# decoder that quietly returns nonsense for a whole model family, the claim is withdrawn.
# The evidence, including the raw 0x16 frame, is in DECODING.md 3.9.
# Anything not handled below now raises the "Unknown Message" warning, which is the honest
# outcome: visible, rather than plausible and wrong.

#Button CC equivs
BTN_CLEAR_RAY = 239
BTN_P1 = 228
BTN_P2 = 229
BTN_TEMP_DOWN = 226
BTN_TEMP_UP = 225
BTN_MENU =254
BTN_LIGHT_ON = 241
BTN_LIGHT_COLOR = 242
BTN_NA = 224
PUMP_BUTTONS = (BTN_P1, BTN_P2, BTN_CLEAR_RAY) #indexed like pump_status / target_pump_status
TEMP_BUTTONS = (BTN_TEMP_UP, BTN_TEMP_DOWN)

#Used to find our old channel, or an open channel
DETECT_CHANNEL_STATE_START = 0
DETECT_CHANNEL_STATE_CHANNEL_NOT_FOUND = 5 #Wait this man CTS cycles before deciding that a channel is available to use
NO_CHANGE_REQUESTED = -1 #Used to return control to other devices
TEMP2_FLAG_SHIFT = 7 #TEMP_FEILD_2 bit 7 is a flag, not temperature data
TEMP2_VALUE_MASK = 0x7F #TEMP_FEILD_2 the temperature is the low 7 bits
TEMP2_PLAUSIBLE_MIN = 40.0 #Below this the second sensor reading is treated as undecoded, not cold
TEMP2_PLAUSIBLE_MAX = 115.0 #Above this likewise - see DECODING.md 3.4
CHECKS_BEFORE_RETRY = 2 #How many status messages we should receive before retrying our command
RECONNECT_BACKOFF_START = 5 #s before the 2nd reconnect attempt; doubles per failure
RECONNECT_BACKOFF_MAX = 60 #s cap, so an hour-long outage costs ~60 attempts, not ~3000
FILTER_DAYS_KEY = 250 #UNKOWN_FIELD_3 = this XOR the FC days: 250 at FC 0, 238 / 228 / 210 at FC 20 / 30 / 40
IDLE_SECONDS = 8 #no press on the bus for this long and the panel is home (it times out in ~5 s)


def date_candidates(field, year):
    """Every (month, day) DATE_FIELD_2 can mean. It holds (day << 3) XOR month.

    Seen setting the date on the panel: Apr 3 read 28 = 24 ^ 4 and Oct 25 read
    194 = 200 ^ 10 (DECODING.md 3.3). A month of 8-12 flips bit 3, which is also day bit 0, so
    when month & 7 is 1-4 the field alone cannot tell month m from m + 8: 137 is Jan 17 or Sep 16.
    """
    out = []
    for month in range(1, 13):
        if month & 7 != field & 7:
            continue
        day = (field ^ month) >> 3
        try:
            date(year if year > 0 else 2000, month, day)
        except ValueError:
            continue
        out.append((month, day))
    return out


def pick_date(cands, prev_cands, prev_pick, year, today=None):
    """Choose among date_candidates(): continuity first, then the reading nearest today.

    A day change separates the two readings - Jan 17 -> 18 is 137 -> 145, Sep 16 -> 17 would
    be 137 -> 129 - so the candidate that is one of the previous candidates, or the day after
    one, wins. Only with no history (start-up, or a date just set) does nearness to today
    decide, which is right for a tub whose date is kept set.
    """
    if not cands:
        return -1, -1
    if prev_pick in cands:
        return prev_pick
    if prev_cands:
        def follows(c):
            for p in prev_cands:
                try:
                    if date(2001, *c) - date(2001, *p) in (timedelta(0), timedelta(1)):
                        return True
                except ValueError:   # Feb 29 in a non-leap reference year
                    pass
                if c == (1, 1) and p == (12, 31):
                    return True
            return False
        succ = [c for c in cands if follows(c)]
        if len(succ) == 1:
            return succ[0]
    today = today or date.today()

    def distance(md):
        return abs((date(year if year > 0 else today.year, *md) - today).days)
    return min(cands, key=distance)


class SundanceRS485(BalboaSpaWifi):
     



    def __init__(self, hostname, port=8899):
        super().__init__(hostname, port)
               
        #debug
        logging.basicConfig()
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.DEBUG)
        self.reconnect_attempts = 0 #consecutive reconnects with no good frame since
        self.recorder = self._open_recorder(os.environ.get("RAW_FRAME_LOG"))
        
        #Hard code some values that the base class needs and we dont know how to auto detect yet
        self.config_loaded = True
        self.pump_array = [1, 1, 1, 0, 0, 0]
        self.nr_of_pumps = 3
        self.circ_pump = 1
        self.tempscale = self.TSCALE_F #Can probably be determined...
        self.timescale = self.TIMESCALE_24H 
        self.temprange = 1
        
        self.filter_mode = 1 #Can probably be determined...
        self.heatmode = 0 #Can probably be determined...
        # Measured off the wire, not guessed: across 1427 frames every frame with
        # automatic circulation fell in spa hours 08:00-15:59 and every frame outside
        # that window had it off, so the default schedule is 08:00 for 8 hours.
        # See DECODING.md 3.7.
        self.filter1_hour = 8
        self.filter1_duration_hours = 8 #Can probably be determined...
        self.filter2_enabled = 0  #Can probably be determined...
     
        #Setup some Model Specific Values
        self.day = -1
        self.month = -1
        self.year = -1
        self.temp2 = -1
        self.manualCirc = -1
        self.autoCirc = -1
        self.unknownCirc = -1
        self.heatState2 = -1      
        self.displayText = -1
        self.heatMode = -1
        self.UnknownField3 = -1
        self.UnknownField9 = -1
        self.panelLock = -1 #Assuming this can be determiend eventaully
        self.today = date.today #the real date, for pick_date's tie-break; tests replace it
        self.dateCands = [] #every (month, day) the last date field could mean, see pick_date
        self.editField = -1 #f15: the value on the panel while a menu field is being edited
        self.filterDays = -1 #FC days, from UNKOWN_FIELD_3
        self.waterDays = -1 #H2O days, UNKOWN_FIELD_12
        self.menuJobs = [] #panel_menu.MenuJob, run one at a time
        self.menuStatus = "idle"
        # DISPLAY_FIELD = panel state XOR the UV days, and on the home screen (state 0) it IS the
        # UV days (DECODING.md 3.12). So the UV days are learned whenever the panel must be home -
        # nobody has pressed anything for IDLE_SECONDS - and every state is decoded with them.
        self.uvDays = -1
        self.panelState = -1
        self.lastPressAt = 0.0
        # Kept so nothing the frames carry is thrown away (DECODING.md 3.8): the decoded fields,
        # the first byte of every folded pair, and the odd byte the fold never reads.
        self.c4Fields = []
        self.c4PairFirsts = None
        self.c4Tail = None
        self.lightFields = []
        self.caPairs = None
        
        self.lightBrightnes = -1
        self.lightMode = -1
        self.lightR = -1
        self.lightG = -1
        self.lightB = -1
        self.lightCycleTime = -1

        self.circMode = "unknown"
        self.circB5 = -1
        self.circB6 = -1
        self.temp2Valid = None
        self.temp2Flag = -1
     
        #setup some sepcific items that we need that the base class doenst
        self.queue = queue.Queue() #Messages must e sent on CTS for our channel, not any time
        self.channel = None     #The channel we are assigned to
        self.discoveredChannels = [] #all the channels the tub is prodcign CTS's for
        self.activeChannels = [] #Channels we know are in use by other RS485 devices
        self.detectChannelState = DETECT_CHANNEL_STATE_START #STate machine used to find an open channel, or to get us a new one
        self.target_pump_status  = [NO_CHANGE_REQUESTED, NO_CHANGE_REQUESTED, NO_CHANGE_REQUESTED, NO_CHANGE_REQUESTED, NO_CHANGE_REQUESTED, NO_CHANGE_REQUESTED] #Not all messages seem to get accepted, so we have to check if our change compelted and retry if needed
        self.targetTemp = NO_CHANGE_REQUESTED
        
        self.targetlightMode = NO_CHANGE_REQUESTED
        self.targetlightBrightnes = NO_CHANGE_REQUESTED
        
        
        self.UnknownField3 = 0
        self.UnknownField9 = 0           
        self.UnknownField12  =  0
        
        self.attemptsToCommand = 0
        
        self.checkCounter = 0
        self.checkCounterL = 0
        self.CAprior_status = None
        self.lastRGBMode = "White"
        self.lastBrightness = 100
        
        self.displayTextS = "unknown"    
        self.heatModeText ="unknown"  

        self.HEAT_MODE_MAP = [
            [32,"AUTO"],
            [34,"ECO"],
            [36,"DAY"],
        ]

        # Panel states (DISPLAY_FIELD XOR the UV days), from the panel walk; panel_menu owns them.
        self.DISPLAY_MAP = sorted(panel_menu.SCREEN_NAME.items())

        self.LIGHT_MODE_MAP = [
            [128,"Fast Blend"], #with 2 second constant
            [127,"Slow Blend"], #wiht 4 secodn constant
            [255,"Frozen Blend"],
            [2,"BLue"],
            [7,"Violet"],
            [6,"Red"],
            [8,"Amber"],
            [3,"Green"],
            [9,"Aqua"],
            [1,"White"],
            [0,"Off"],
            [-1,"No Change"],
        ]
       

 
    async def connect(self):
        """ Connect to the spa."""
        if self.writer is not None:
            # Never hold two sessions at once: the EW11 allows only two clients.
            self.writer.transport.abort()
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=15
            )
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
            self.log.error(
                "Cannot connect to spa at {0}:{1}: {2}".format(self.host, self.port, e)
            )
            self.connected = False
            return False
        except Exception as e:
            self.log.error(f"Error connecting to spa at {self.host}:{self.port}: {e}")
            self.connected = False
            return False
        self.connected = True
        sock = self.writer.transport.get_extra_info('socket')
        self.log.info(str(sock))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return True

    async def send_temp_change(self, newtemp):
        """ Change the set temp to newtemp. """
        # Check if the new temperature is valid for the current heat mode
        if (
            newtemp < self.tmin[self.temprange][self.tempscale]
            or newtemp > self.tmax[self.temprange][self.tempscale]
        ):
            self.log.error("Attempt to set temperature outside of heat mode boundary")
            return
        self._drop_queued_presses(*TEMP_BUTTONS)
        self.attemptsToCommand = 0
        self.targetTemp = newtemp

    async def change_light(self, light, newstate):
        self.log.info("Not supported with New Format messaging")
        return 

    async def change_pump(self, pump, newstate):
        """ Change pump #pump to newstate. """
        # sanity check
        if (
            pump >= len(PUMP_BUTTONS)
            or newstate > self.pump_array[pump]
        ):
            return
        # Always retarget, even when the pump already reads newstate: an opposite request may
        # still be pending, and the newest request must replace it (an "off" sent while an
        # "on" is in progress cancels the "on"). The status-frame loop clears a target that
        # is already met without pressing anything.
        self._drop_queued_presses(PUMP_BUTTONS[pump])
        self.attemptsToCommand = 0
        self.target_pump_status[pump] = newstate

    def _drop_queued_presses(self, *buttons):
        """Forget presses for these buttons that are queued but not yet sent.

        A press is queued on one status frame and sent on our next clear-to-send, so a new
        target can arrive in between; sent anyway, the press still serves the OLD target.
        Measured: set 100 from 95, then 95 while at 99 - an UP queued for 100 went out after
        the 95 arrived, 99 -> 100 -> 95. For a toggle (a pump, ClearRay) a stale press is
        worse: it turns on what was just turned off. Callers retarget right after this.
        """
        with self.queue.mutex:
            kept = [msg for msg in self.queue.queue if msg[5] not in buttons]
            self.queue.queue.clear()
            self.queue.queue.extend(kept)

    async def send_CCmessage(self, val):
        """ Sends a message to the spa with variable length bytes. """    
        # if not connected, we can't send a message
        if not self.connected:
            self.log.info("Tried to send CC message while not connected")
            return

        # if we dont have a channel number yet, we cant form a message
        if self.channel is None:
            self.log.info("Tried to send CC message without having been assigned a channel")
            return
            
        if self.attemptsToCommand > 64:
            self.log.info("Tried {} times to change state {} giving up.".format( self.attemptsToCommand, val))
            
        # Exampl: 7E 07 10 BF CC 65 85 A6 7E 
        message_length = 7
        data = bytearray(9)
        data[0] = M_STARTEND
        data[1] = message_length
        data[2] = self.channel
        data[3] = 0xBF
        data[4] = CC_REQ
        data[5] = val
        data[6] = 0
        data[7] = self.balboa_calc_cs(data[1:message_length], message_length - 1)
        data[8] = M_STARTEND

        self.log.debug(f"queueing message: {data.hex()}")
        self.queue.put(data)
        self.lastPressAt = time.time()
        
        self.attemptsToCommand += 1

    async def send_message(self, *bytes):
        """ Sends a message to the spa with variable length bytes. """
        self.log.info("Not supported with New Format messaging")
        return 
        
    def xormsg(self, data):
        """Fold a payload in pairs: c[i] = d[2i] ^ d[2i+1] ^ 1. This is the decode.

        It keeps only the XOR of each pair. What it does not keep is not dropped any more:
        unfold() returns the first byte of every pair and the odd byte left over (a C4 payload
        is 33 bytes), and both reach the log and the raw recorder. DECODING.md 3.8.
        """
        return [data[i] ^ data[i + 1] ^ 1 for i in range(0, len(data) - 1, 2)]

    @staticmethod
    def unfold(data):
        """The bytes xormsg() does not keep, with the frame's key taken out.

        The first byte of every pair, and the odd last byte, are a per-frame random key XOR a
        fixed pattern: between any two frames every one of them differs by the same XOR
        (measured on C4 and CA alike). So they are returned relative to the first one -
        the pattern, which is what could carry data - and the key itself is not worth keeping.
        RAW_FRAME_LOG still records the raw bytes.
        """
        firsts = list(data[0:len(data) - 1:2])
        tail = list(data[len(data) - 1:]) if len(data) % 2 else []
        key = firsts[0] if firsts else 0
        return [b ^ key for b in firsts], [b ^ key for b in tail]

    def _panel_in_menu(self):
        """True while the panel shows a menu screen or an open edit - or might.

        On those screens the temperature buttons open and change the menu field and Pump 1
        accepts it, so a set-point or pump press sent now would edit a setting instead. Until the
        UV key is learned the state is unknown, and a press is held then too.
        """
        return self.panelState not in panel_menu.NOT_MENU

    async def parse_C4status_update(self, data):
        """Parse a status update from the spa.
        01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29
        7E 26 FF AF C4 AE A7 AA AB A4 A1 C9 5D A5 A1 C2 A1 9C BD CE BB E2 B9 BB AD B4 B5 A7 B7 DF B1 B2 9B D3 8D 8E 8F 88 F9 7E
        """
        self.lastC4MessageReceived = time.time()

        payload = data[5:len(data)-2]
        pairFirsts, tail = self.unfold(payload)
        #"Decrypt" / Decode the message
        data = self.xormsg(payload)

        """Parse a status update from the spa.
        01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29
        [9, 0, 5, 148, 5, 99, 32, 124, 96, 23, 0, 33, 110, 39, 96, 0]
        """

        HOUR_FIELD = 0 #XOR 6 to get 24hour time
        PUMP_FIELD_1 = 1 #Most bit data
        PUMP_FIELD_0 = 2 #Dont know how to use this yet...
        UNKOWN_FIELD_3 = 3 #FC days XOR FILTER_DAYS_KEY (two readings, DECODING.md 3.11)
        TBD_FIELD_4 = 4 #5 When Everything Off. 69 when clear ray / circ on?
        TEMP_FIELD_1 = 5 #Devide by 2 if in C, otherwise F
        HEAT_MODE_FIELD = 6 #
        DATE_FIELD_2 = 7 #(day << 3) XOR month, see date_candidates
        SET_TEMP_FIELD = 8 #Devide by 2 if in C, otherwise F
        UNKOWN_FIELD_9 = 9 #YEAR - 2000 (was guessed to be a service counter; it is the year)
        HEATER_FIELD_1 = 10 #= 64 when Heat on
        MINUTE_FIELD = 11 #Ok as is
        UNKOWN_FIELD_12 = 12 #H2O days, plain (two readings, DECODING.md 3.11)
        DISPLAY_FIELD = 13
        TEMP_FEILD_2 = 14 #Appears to be 2nd temp sensor C  or F directly. Changes when pump is on!
        EDIT_FIELD = 15 #the value on the panel while a menu field is being edited; 0 otherwise

        PUMP_0_BIT_SHIFT = 4 #PUMP_FIELD_0 b10,000 When pump running
        PUMP_1_BIT_SHIFT = 2 #PUMP_FIELD_1 b00,100 When pump running
        # Circulation (DECODING.md 3.1), from captures with both automatic and ClearRay /
        # manual circulation. In AUTO alone these four bits agree and look redundant; they
        # are not: across all 420 circulating frames MANUAL_CIRC_SHIFT and AUTO_CIRC_SHIFT
        # are perfectly complementary, with no exceptions.
        CIRC_RUNNING_SHIFT = 6 #TBD_FIELD_4  b1,000,000 circulation running, either mode
        AUTO_CIRC_SHIFT = 7 #HEATER_FIELD_1  b10,000,000 circulation running AUTOMATICALLY
        MANUAL_CIRC_SHIFT = 7 #PUMP_FIELD_1  b10,000,000 circulation running MANUALLY (ClearRay)
        # f1 b5/b6 also move with circulation but lag it across transitions; kept as
        # diagnostics rather than named, because what separates them is not yet known.
        CIRC_B5_SHIFT = 5 #PUMP_FIELD_1
        CIRC_B6_SHIFT = 6 #PUMP_FIELD_1


        TEMP_FIELD_1_xor = 2 #TEMP_FIELD_1 need to Xor this field by 2 to get actual temperature for some reason
        HEATER_SHIFT_1 = 6 #HEATER_FIELD_1 b1000000 when Heat on
        HOUR_XOR = 6 #HOUR_FIELD Need to xor the hour field with 6 to get actual hour

        TBD_4_CIRC_SHIFT = 6 #TBD_FIELD_4 Field 4 goes up by 64 when circ is running it seems

        #High Confidance
        time_hour = data[HOUR_FIELD]^HOUR_XOR
        time_minute = data[MINUTE_FIELD]

        pump0 = (data[PUMP_FIELD_0] >> PUMP_0_BIT_SHIFT) & 1
        pump1 = (data[PUMP_FIELD_1] >> PUMP_1_BIT_SHIFT) & 1
        circ_pump_status = (data[TBD_FIELD_4] >> CIRC_RUNNING_SHIFT) & 1
        autoCirc = (data[HEATER_FIELD_1] >> AUTO_CIRC_SHIFT) & 1
        manualCirc = (data[PUMP_FIELD_1] >> MANUAL_CIRC_SHIFT) & 1
        circB5 = (data[PUMP_FIELD_1] >> CIRC_B5_SHIFT) & 1
        circB6 = (data[PUMP_FIELD_1] >> CIRC_B6_SHIFT) & 1
        if manualCirc:
            circMode = "manual"
        elif autoCirc:
            circMode = "auto"
        else:
            circMode = "off"

        settemp = float(data[SET_TEMP_FIELD])
        settemp = settemp / (2 if self.tempscale == self.TSCALE_C else 1)
        temp = data[TEMP_FIELD_1]^TEMP_FIELD_1_xor
        curtemp = (
            temp / (2 if self.tempscale == self.TSCALE_C else 1)
            if temp != 255
            else None
        )

        heatstate = (data[HEATER_FIELD_1] >> HEATER_SHIFT_1) & 1
        displayText = data[DISPLAY_FIELD]
        # DISPLAY_FIELD = panel state XOR the UV days (DECODING.md 3.12). Values such as
        # 130-132 with the panel idle are the UV days alone, counting down.
        if time.time() - self.lastPressAt > IDLE_SECONDS and not (self.menuJobs and self.menuJobs[0].started):
            self.uvDays = displayText
        panelState = displayText ^ self.uvDays if self.uvDays >= 0 else -1
        heatMode = data[HEAT_MODE_FIELD]

        # UNKOWN_FIELD_9 is the YEAR - 2000: it read 14 until the date was set on the panel,
        # then 25 and 26 as the year was stepped (DECODING.md 3.3).
        year = 2000 + data[UNKOWN_FIELD_9]
        dateCands = date_candidates(data[DATE_FIELD_2], year)
        month, day = pick_date(dateCands, self.dateCands, (self.month, self.day), year, self.today())
        editField = data[EDIT_FIELD] if len(data) > EDIT_FIELD else -1

        displayTextS = "unknown"
        for x,y in self.DISPLAY_MAP:
            if x == panelState:
               displayTextS = y
               break

        heatModeText = "unknown"
        for x,y in self.HEAT_MODE_MAP:
            if x == heatMode:
               heatModeText = y
               break

        #Medium Confidance
        unknownCirc = (data[TBD_FIELD_4] >> TBD_4_CIRC_SHIFT) & 1
        # f14 bit 7 is a FLAG, not part of the temperature. Masking it turns the three
        # "raw 193" frames that looked like outliers into 193 & 0x7F = 65, +32 = 97 = the
        # main sensor exactly. Meaning of the flag is unknown (n=3, all during automatic
        # circulation) - published as a diagnostic rather than guessed at. DECODING.md 3.4.
        temp2Flag = (data[TEMP_FEILD_2] >> TEMP2_FLAG_SHIFT) & 1
        temp2 = float(data[TEMP_FEILD_2] & TEMP2_VALUE_MASK)
        # Use THIS frame's circulation state, not self.circ_pump_status, which is still the
        # previous frame's; the previous frame's state would put temp2 32F out for one frame
        # on every circulation transition.
        if(circ_pump_status == 1):
            temp2 = temp2 + 32
        # The +32 correction holds in 250 of 263 circulating frames, but a raw 193 has been
        # observed, which would surface as 225F. Publish only plausible water temperatures;
        # keep the uncorrected value in temp2 so change-detection below is unaffected.
        temp2Valid = temp2 if (TEMP2_PLAUSIBLE_MIN <= temp2 <= TEMP2_PLAUSIBLE_MAX) else None

        #Low Confidance - two readings each, from the panel walk (DECODING.md 3.11)
        UnknownField3 = data[UNKOWN_FIELD_3]
        UnknownField9 = data[UNKOWN_FIELD_9]
        UnknownField12 = data[UNKOWN_FIELD_12]
        filterDays = UnknownField3 ^ FILTER_DAYS_KEY
        waterDays = UnknownField12

        # Every byte that moved, decoded or not, and the pair bytes the fold does not keep, so
        # a frame where only a known field (a pump, the set point) moved is logged too.
        prior = self.prior_status
        changed = [i for i in range(len(data)) if prior is None or i >= len(prior) or data[i] != prior[i]]
        pairsChanged = (pairFirsts, tail) != (self.c4PairFirsts, self.c4Tail)

        self.time_hour = time_hour
        self.time_minute = time_minute
        self.pump_status[0] = pump0
        self.pump_status[1] = pump1
        self.circ_pump_status = circ_pump_status
        self.settemp = settemp
        self.curtemp = curtemp
        self.heatstate = heatstate
        self.displayText = displayText
        self.heatMode = heatMode
        self.year = year
        self.dateCands = dateCands
        self.month = month
        self.day = day
        self.editField = editField
        self.autoCirc = autoCirc
        self.manualCirc = manualCirc
        self.circMode = circMode
        self.circB5 = circB5
        self.circB6 = circB6
        self.panelState = panelState
        self.temp2Valid = temp2Valid
        self.temp2Flag = temp2Flag
        # The ClearRay button is what this index commands, and manual circulation is what
        # that button actually asserts - so track it, not "circulation running", or the
        # switch can never reflect what it controls.
        self.pump_status[2] = manualCirc

        self.displayTextS = displayTextS
        self.heatModeText = heatModeText

        #Medium Confidance
        self.unknownCirc = unknownCirc
        self.temp2 = temp2

        #Low Confidance
        self.UnknownField3 = UnknownField3
        self.UnknownField9 = UnknownField9
        self.UnknownField12  =  UnknownField12
        self.filterDays = filterDays
        self.waterDays = waterDays
        self.c4Fields = list(data)
        self.c4PairFirsts = pairFirsts
        self.c4Tail = tail

        # Decide presses on THIS frame's state: this runs after the attributes above are
        # updated, so every target is compared against the current frame, not the previous
        # one. A menu job, while it runs, owns the buttons outright.
        if not await self._step_menu(panelState, editField, displayText):
            await self._step_commands()

        if not changed and not pairsChanged:
            return

        self.lastupd = time.time()

        if changed:
            self.log.info("Time: {}".format(datetime.fromtimestamp(self.lastupd).strftime("%Y-%m-%d %H:%M:%S") ))
            self.log.info("Full Message: C4: {}  Changed: {}".format(data, " ".join(
                "f{}:{}->{}".format(i, "-" if prior is None else prior[i], data[i]) for i in changed)))
            self.log.info("{:04d}-{:02d}-{:02d} {:02d}:{:02d} P0:{} P1:{} Circ:{} settemp:{} curtemp:{} heatstate:{} display:{} state:{} {} edit:{} heatMode:{} {} FC:{} UV:{} H2O:{} temp2:{}".format(
                year, month, day, time_hour, time_minute, pump0, pump1, circ_pump_status, settemp, curtemp,
                heatstate, displayText, panelState, displayTextS, editField, heatMode, heatModeText,
                filterDays, self.uvDays, waterDays, temp2))
        if pairsChanged:
            self.log.info("C4 pair pattern: {} tail: {}".format(pairFirsts, tail))

        self.prior_status = bytearray(data)

        await self.int_new_data_cb()

    def _commands_pending(self):
        return (self.targetTemp != NO_CHANGE_REQUESTED
                or any(t != NO_CHANGE_REQUESTED for t in self.target_pump_status)
                or self.targetlightMode != NO_CHANGE_REQUESTED
                or self.targetlightBrightnes != NO_CHANGE_REQUESTED)

    async def _step_commands(self):
        """Set point and pump targets: one press per CHECKS_BEFORE_RETRY frames until met."""
        #FIND OUT IF OUR LAST COMMAND WORKED...
        if self.checkCounter > 0:
            self.checkCounter -= 1
        if self._panel_in_menu():
            return   # a person is in the menus; pressing now would edit a setting

        if (self.checkCounter == 0):
            if(self.settemp  != self.targetTemp and self.targetTemp != NO_CHANGE_REQUESTED):
                if self.targetTemp < self.settemp:
                    await self.send_CCmessage(BTN_TEMP_DOWN)
                else:
                    await self.send_CCmessage(BTN_TEMP_UP)
                self.checkCounter = CHECKS_BEFORE_RETRY
            elif self.settemp  == self.targetTemp:
                self.targetTemp = NO_CHANGE_REQUESTED

        if (self.checkCounter == 0):
            for i in range(0,len(self.target_pump_status)):
                if self.pump_status[i] != self.target_pump_status[i] and self.target_pump_status[i] != NO_CHANGE_REQUESTED:
                    await self.send_CCmessage(PUMP_BUTTONS[i]) #Pump 1, Pump 2, Clear Ray / Circulating Pump
                    self.checkCounter = CHECKS_BEFORE_RETRY
                elif self.pump_status[i] == self.target_pump_status[i]:
                    self.target_pump_status[i] = NO_CHANGE_REQUESTED

    async def _step_menu(self, screen, edit, raw_screen):
        """Run the head of the menu-job queue on this frame. True while a job owns the buttons."""
        while self.menuJobs:
            job = self.menuJobs[0]
            if not job.started:
                if self._commands_pending():
                    return False   # let set-point / pump / light presses finish first
                why = job.precheck(self) if job.precheck else None
                if why:
                    job.fail(why)
                elif screen != panel_menu.HOME:
                    # Someone is at the panel, or it shows an alert or a flashing set point.
                    job.idle_frames += 1
                    if job.idle_frames <= panel_menu.START_FRAMES:
                        self.menuStatus = "waiting for an idle panel: %s" % job.name
                        return True
                    job.fail("the panel never went back to the temperature screen")
                else:
                    job.started = True
                    job.idle_frames = 0
                    self.log.info("Panel menu: starting %s", job.name)
            if not job.finished:
                if self.uvDays < 0:
                    job.fail("the display key (UV days) is not known yet - wait for an idle panel")
                    button = None
                else:
                    button = job.on_frame(screen, edit, ctx={"minute": self.time_minute,
                                                             "raw_screen": raw_screen})
                if job.new_uv is not None:
                    self.uvDays, job.new_uv = job.new_uv, None   # the UV save re-keys the display
                if button is not None:
                    self.attemptsToCommand = 0   # its "giving up" counter is for retry loops
                    await self.send_CCmessage(button)
                if not job.finished:
                    self.menuStatus = job.status
                    return True
            # Finished, saved or not. Hold the buttons until the panel times out to the
            # temperature screen by itself: on the menu screen it is left on, a set-point UP
            # would open that edit again. Then read the setting back - after a failure too,
            # because the panel's timeout keeps whatever an open edit had reached.
            if job.started and screen != panel_menu.HOME:
                job.idle_frames += 1
                if job.idle_frames <= panel_menu.START_FRAMES:
                    return True
            if job.status.startswith("done") and job.check:
                why = job.check(self)
                job.status = ("failed: %s - saved, but %s" % (job.name, why)) if why else \
                    "done: %s, reads back correctly" % job.name
            elif job.started:
                job.status += "; the spa now reads %04d-%02d-%02d %02d:%02d" % (
                    self.year, self.month, self.day, self.time_hour, self.time_minute)
            self.menuStatus = job.status
            self.log.info("Panel menu: %s", job.status)
            self.menuJobs.pop(0)
            if job.status.startswith("failed") and self.menuJobs:
                self.log.info("Panel menu: dropping %d queued job(s) after a failure", len(self.menuJobs))
                self.menuJobs.clear()
        return False

    def _queue_menu_job(self, job):
        self.menuJobs.append(job)
        self.menuStatus = "queued: %s" % job.name
        self.log.info("Panel menu: queued %s", job.name)

    async def set_date(self, year, month, day):
        """Set the spa's date through the DATE menu. ValueError if it is not a date."""
        self._queue_menu_job(panel_menu.date_job(year, month, day))

    async def set_time(self, hour, minute):
        """Set the spa's clock through the TIME menu. ValueError if it is not a time."""
        self._queue_menu_job(panel_menu.time_job(hour, minute, self.time_hour))

    async def set_service_days(self, name, days):
        """FC / UV / H2O days through their menus. ValueError if out of range."""
        self._queue_menu_job(panel_menu.service_job(name, days, self.uvDays))

    async def parse_CA_light_status_update(self, data):
        """Parse a status update from the spa.
        01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29
        7E 22 FF AF CA 8A 36 CA CB C4 C5 C6 FB C0 C1 C2 3C DC DD DE DF D8 D9 DA DB D4 D5 D6 D7 D0 D1 D2 D3 EC E5 7E
        """
        payload = data[5:len(data)-2]
        pairFirsts, tail = self.unfold(payload)
        #"Decrypt" the message
        data = self.xormsg(payload)

        """Parse a status update from the spa.
        01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29
        TODO Example after decryption
        """
        #TODO: The rest... - the layout below is by byte position with no recorded correlation
        # (DECODING.md 3.5); every byte is kept in lightFields and logged on change.

        self.lightBrightnes = data[1]
        self.lightMode = data[4]
        self.lightB = data[2]
        self.lightG = data[6]
        self.lightR = data[8]
        self.lightCycleTime= data[9]
        self.lightUnknown1 = data[0]
        self.lightUnknown3 = data[3]
        self.lightUnknown4 = data[5]
        self.lightUnknown7 = data[7]
        self.lightFields = list(data)

        self.lightModeText = "unknown"
        for x,y in self.LIGHT_MODE_MAP:
            if x == self.lightMode:
               self.lightModeText = y
               break

        if self.checkCounterL > 0:
            self.checkCounterL -= 1
        # The light buttons are not menu buttons as far as is known, but a running menu job
        # still owns the bus: one press at a time, and none it did not plan.
        busy = bool(self.menuJobs and self.menuJobs[0].started) or self._panel_in_menu()

        if (self.checkCounterL == 0 and not busy):
            if(self.targetlightMode  != self.lightMode and self.targetlightMode != NO_CHANGE_REQUESTED):
                await self.send_CCmessage(BTN_LIGHT_COLOR)
                self.checkCounterL = CHECKS_BEFORE_RETRY
            elif self.targetlightMode  == self.lightMode:
                self.targetlightMode = NO_CHANGE_REQUESTED

        if (self.checkCounterL == 0 and not busy):
            if(self.targetlightBrightnes  != self.lightBrightnes and self.targetlightBrightnes != NO_CHANGE_REQUESTED):
                await self.send_CCmessage(BTN_LIGHT_ON)
                self.checkCounterL = CHECKS_BEFORE_RETRY
            elif self.targetlightBrightnes  == self.lightBrightnes:
                self.targetlightBrightnes = NO_CHANGE_REQUESTED

        prior = self.CAprior_status
        if prior is not None and list(prior) == list(data) and (pairFirsts, tail) == self.caPairs:
            return

        self.log.info("CA{}  pair pattern: {} tail: {}".format(data, pairFirsts, tail))
        self.lastupd = time.time()
        self.CAprior_status = bytearray(data)
        self.caPairs = (pairFirsts, tail)

    def _open_recorder(self, path):
        """RAW_FRAME_LOG=<file>: one JSON line per frame, raw bytes as hex, rotating.

        Opt-in. It keeps what the decode cannot: both bytes of every folded pair, the odd tail
        byte, and frame types nothing parses (DECODING.md 3.8). Skipped: the bus's
        clear-to-send polls and the "nothing pressed" (224) replies every panel sends on its
        turn - several a second, and they carry nothing.
        """
        if not path:
            return None
        rec = logging.getLogger(__name__ + ".frames")
        rec.propagate = False
        rec.setLevel(logging.INFO)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=int(os.environ.get("RAW_FRAME_LOG_BYTES", 20 * 1024 * 1024)),
            backupCount=int(os.environ.get("RAW_FRAME_LOG_FILES", 5)))
        handler.setFormatter(logging.Formatter("%(message)s"))
        rec.addHandler(handler)
        self.log.info("Recording raw frames to %s", path)
        return rec

    def _record(self, data):
        mtype = data[4] if len(data) > 4 else -1
        if mtype in (CLEAR_TO_SEND, CLIENT_CLEAR_TO_SEND, NOTHING_TO_SEND):
            return
        if mtype == CC_REQ and len(data) > 6 and data[5] ^ data[6] == BTN_NA:
            return
        self.recorder.info(json.dumps({"t": round(time.time(), 3), "type": "%02X" % mtype,
                                       "hex": bytes(data).hex()}))

    async def setMyChan(self, chan):
        self.channel = chan
        self.log.info("Got assigned channel = {}".format(self.channel))
        message_length = 7
        self.NTS = bytearray(9)
        self.NTS[0] = M_STARTEND
        self.NTS[1] = message_length
        self.NTS[2] = self.channel
        self.NTS[3] = 0xBF
        self.NTS[4] = CC_REQ
        self.NTS[5] = 0 #Dummy
        self.NTS[6] = 0
        self.NTS[7] = self.balboa_calc_cs(self.NTS[1:message_length], message_length - 1)
        self.NTS[8] = M_STARTEND

    def _reset_channel_state(self):
        """Forget our channel assignment so we renegotiate after a reconnect.

        After a dropped/reconnected socket the old channel may be stale or
        conflict, so we start channel discovery from scratch and drop any
        commands that were queued against the dead connection.
        """
        self.channel = None
        self.detectChannelState = DETECT_CHANNEL_STATE_START
        self.discoveredChannels = []
        self.activeChannels = []
        self.queue = queue.Queue()

    async def listen(self):
        """ Listen to the spa babble forever. """

        while True:
            if not self.connected:
                # The socket dropped (or stalled and was marked dead). Try to
                # reconnect and renegotiate a channel instead of silently
                # spinning forever. Back off while attempts keep failing: a
                # gateway that accepts and at once closes would otherwise be
                # hammered (and logged) about once a second.
                delay = 0
                if self.reconnect_attempts:
                    delay = min(RECONNECT_BACKOFF_MAX,
                                RECONNECT_BACKOFF_START * 2 ** (self.reconnect_attempts - 1))
                self.reconnect_attempts += 1
                self.log.error("Not connected to spa; reconnect attempt {0} in {1}s".format(
                    self.reconnect_attempts, delay))
                await asyncio.sleep(delay)
                self._reset_channel_state()
                if await self.connect():
                    self.log.info("Reconnected to spa; renegotiating channel.")
                continue

            data = await self.read_one_message()
            if data is None:
                #await asyncio.sleep(0.0001)
                continue
            # A whole frame with a good CRC: the link works again.
            self.reconnect_attempts = 0
            if self.recorder is not None:
                self._record(data)

            channel = data[2]
            mid = data[3]
            mtype = data[4]

            #print("a")


            if mtype == STATUS_UPDATE:
                await self.parse_C4status_update(data)
            elif mtype == LIGHTS_UPDATE:
                await self.parse_CA_light_status_update(data)
            elif mtype == CLIENT_CLEAR_TO_SEND:
                if self.channel is None and self.detectChannelState == DETECT_CHANNEL_STATE_CHANNEL_NOT_FOUND:
                    message_length = 8
                    data = bytearray(10)
                    data[0] = M_STARTEND
                    data[1] = message_length
                    data[2] = 0xFE
                    data[3] = 0xBF
                    data[4] = CHANNEL_ASSIGNMENT_REQ #type
                    data[5] = 0x02
                    data[6] = 0xF1 #random Magic
                    data[7] = 0x73
                    data[8] = self.balboa_calc_cs(data[1:message_length], message_length - 1)
                    data[9] = M_STARTEND
                    self.writer.write(data)
                    await self.writer.drain()                        
            elif mtype == CHANNEL_ASSIGNMENT_RESPONCE:
                #TODO check for magic numbers to be repeated back
                await self.setMyChan(data[5])
                message_length = 5
                data = bytearray(7)
                data[0] = M_STARTEND
                data[1] = message_length
                data[2] = self.channel
                data[3] = 0xBF
                data[4] = CHANNEL_ASSIGNMENT_ACK #type
                data[5] = self.balboa_calc_cs(data[1:message_length], message_length - 1)
                data[6] = M_STARTEND
                self.writer.write(data) 
                await self.writer.drain()                   
            elif mtype == EXISTING_CLIENT_REQ:                      
                message_length = 8
                data = bytearray(9)
                data[0] = M_STARTEND
                data[1] = message_length
                data[2] = self.channel
                data[3] = 0xBF
                data[4] = EXISTING_CLIENT_RESPONCE #type
                data[5] = 0x04 #Dont know!
                data[6] = 0x08 #Dont know!
                data[7] = 0x00 #Dont know!
                data[8] = self.balboa_calc_cs(data[1:message_length], message_length - 1)
                data[9] = M_STARTEND
                self.writer.write(data)
                await self.writer.drain()
            elif mtype == CLEAR_TO_SEND:               
                if not channel in  self.discoveredChannels:
                    self.discoveredChannels.append(data[2])
                    #print("Discovered Channels:" + str(self.discoveredChannels))
                    #detec conflict
                    if data[2] == self.channel:
                        self.log.warn("Found a channel conflict, getting a new channel")
                        self.channel = None
                        self.detectChannelState = DETECT_CHANNEL_STATE_START
                elif channel == self.channel:
                    # get_nowait, not empty() then get(): _drop_queued_presses runs on the MQTT
                    # thread and can empty the queue in between, and a blocking get() here
                    # would stall this whole event loop.
                    try:
                        msg = self.queue.get_nowait()
                    except queue.Empty:
                        msg = None
                    if msg is None:
                        #self.writer.write(self.NTS)
                        await self.writer.drain()
                    else:
                        self.writer.write(msg)
                        await self.writer.drain()
                        self.log.debug("sent")
            else:
                if (mtype == CC_REQ):
                    if not channel in  self.activeChannels:
                        self.activeChannels.append(data[2])
                        self.log.info("Active Channels:" + str(self.activeChannels))
                    elif  self.detectChannelState < DETECT_CHANNEL_STATE_CHANNEL_NOT_FOUND:
                        self.detectChannelState += 1
                        if self.detectChannelState == DETECT_CHANNEL_STATE_CHANNEL_NOT_FOUND:
                            self.discoveredChannels.sort()
                            for chan in self.discoveredChannels:
                                if not chan in self.activeChannels:
                                    await self.setMyChan( chan)
                                    break
                    buttondata = data[5]^data[6]
                    if buttondata != BTN_NA:
                        self.lastPressAt = time.time()   # someone is at a panel: it may be in a menu
                        self.log.info("Got Button Press {} {} : ".format(data[5]^data[6]^1, buttondata) + "".join(map("{:02X} ".format, bytes(data))))
                elif (mtype > NOTHING_TO_SEND) :
                    self.log.warn("Unknown Message {:02X} {:02X} {:02X} x".format(channel, mid, mtype) + "".join(map("{:02X} ".format, bytes(data))))
              
    async def spa_configured(self):
            return True
        
    async def listen_until_configured(self, maxiter=20):
        """ Listen to the spa babble until we are configured."""
        return True
      
   
   
   
    def get_day(self):
        return self.day
        
    def get_month(self):    
        return self.month 
        
    def get_year(self):  
        return self.year 
        
    def get_temp2(self):  
        return self.temp2 
        
    def get_manualCirc(self):  
        return self.manualCirc 
        
    def get_autoCirc(self):  
        return self.autoCirc
        
    def get_unknownCirc(self):  
        return self.unknownCirc
        
    def get_heatState2(self):  
        return self.heatState2

    def get_displayText(self):  
        return self.displayText 
        
    def get_heatMode(self):  
        return self.heatMode 
        
    def get_UnknownField3(self):  
        return self.UnknownField3
        
    def get_UnknownField9(self):  
        return self.UnknownField9 
        
    def get_panelLock(self):  
        return self.panelLock 
        
    def get_LightBrightnes(self):  
        return self.lightBrightnes
        
    def get_lightMode(self):  
        return self.lightMode
        
    def get_lightR(self):  
        return self.lightR
        
    def get_lightG(self):  
        return self.lightG
        
    def get_lightB(self):  
        return self.lightB 
         
    async def change_rgbbrightness(self, light, newstate):
        if newstate  < 16.5:
            newstate = 0
        elif newstate < 49.5:
            newstate = 33
        elif newstate < 82.25:
            newstate = 66
        else:
            newstate = 100
        self._drop_queued_presses(BTN_LIGHT_ON)
        self.attemptsToCommand = 0
        self.targetlightBrightnes = newstate
   
    async def change_rgbmode(self, light, newstate):
        #cant change mode when off
        if self.lightBrightnes == 0:
            return
        self._drop_queued_presses(BTN_LIGHT_COLOR)
        self.attemptsToCommand = 0
        newmode = -1
        for x,y in self.LIGHT_MODE_MAP:
            if y == newstate:
                newmode = x 
        self.targetlightMode = newmode
