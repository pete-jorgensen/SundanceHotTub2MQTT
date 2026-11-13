try:
    import sundanceRS485
except ImportError:
    import sundanceRS485 as SundanceRS485

import asyncio
import paho.mqtt.client as mqtt
import os
import re
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:          # Python < 3.9
    ZoneInfo = None
from collections import OrderedDict
import json

# All connection settings come from the environment (docker run -e ...). The defaults
# are placeholders, not working values: at minimum set MQTT_HOST and SERIAL_IP.
mqtt_host = os.environ.get("MQTT_HOST", "mqtt.local")
# MQTT_TLS=1 encrypts the broker connection and verifies its certificate against the system
# trust store (or MQTT_CA_FILE), so MQTT_HOST must be the name on the certificate.
mqtt_tls = os.environ.get("MQTT_TLS", "").lower() in ("1", "true", "yes")
mqtt_ca_file = os.environ.get("MQTT_CA_FILE") or None
mqtt_port = int(os.environ.get("MQTT_PORT", "8883" if mqtt_tls else "1883"))
mqtt_user = os.environ.get("MQTT_USER", "")
mqtt_password = os.environ.get("MQTT_PASSWORD", "")

serial_ip = os.environ.get("SERIAL_IP", "spa-bridge.local")
serial_port = int(os.environ.get("SERIAL_PORT", "8899"))
# The spa's local time zone, for "Sync Spa Clock" (the container's own clock is UTC). An IANA name,
# e.g. Europe/London; unset, the sync button refuses rather than set the tub to UTC.
spa_tz = os.environ.get("SPA_TZ") or os.environ.get("TZ") or ""

base_topic = "homeassistant"
flora_name = "hottub"

current_temperature_topic = '{}/current_temperature_topic'.format(flora_name)
temperature_state_topic  = '{}/temperature_state_topic'.format(flora_name)
temperature_command_topic = '{}/temperature_command_topic'.format(flora_name)
mode_state_topic = '{}/mode_state_topic'.format(flora_name)
action_topic = '{}/action_topic'.format(flora_name)
availability_topic = '{}/availability_topic'.format(flora_name)
pump1_command_topic = '{}/pump1_command_topic'.format(flora_name)
pump1_state_topic = '{}/pump1_state_topic'.format(flora_name)
pump2_command_topic = '{}/pump2_command_topic'.format(flora_name)
pump2_state_topic = '{}/pump2_state_topic'.format(flora_name)
ciculationmanual_command_topic = '{}/ciculationmanual_command_topic'.format(flora_name)
ciculationmanual_state_topic = '{}/ciculationmanual_state_topic'.format(flora_name)
ciculationpump_state_topic = '{}/ciculationpump_state_topic'.format(flora_name)
brightness_command_topic  = '{}/brightness_command_topic'.format(flora_name)
brightness_state_topic = '{}/brightness_state_topic'.format(flora_name)
light_command_topic = '{}/light_command_topic'.format(flora_name)
rgb_state_topic  = '{}/rgb_state_topic'.format(flora_name)
effect_state_topic  = '{}/effect_state_topic'.format(flora_name)
effect_command_topic   = '{}/effect_command_topic'.format(flora_name)
light_state_topic = '{}/light_state_topic '.format(flora_name)

spa_time_command_topic = '{}/spa_time_command'.format(flora_name)
spa_date_command_topic = '{}/spa_date_command'.format(flora_name)
sync_clock_command_topic = '{}/sync_clock_command'.format(flora_name)
# Service timers: (key, name, max days, attribute, panel menu)
SERVICE_TIMERS = [
    ('filter_days', 'Filter Change Days', 180, 'filterDays', 'FC'),
    ('uv_days',     'UV Change Days',     250, 'uvDays',     'UV'),
    ('water_days',  'Water Change Days',  360, 'waterDays',  'H2O'),
]
service_command_topic = {k: '{}/{}_command'.format(flora_name, k) for k, _, _, _, _ in SERVICE_TIMERS}
service_state_topic = {k: '{}/{}_state'.format(flora_name, k) for k, _, _, _, _ in SERVICE_TIMERS}
menu_status_topic = '{}/menu_status_state'.format(flora_name)

command_topics = [
    spa_time_command_topic,
    spa_date_command_topic,
    sync_clock_command_topic,
] + list(service_command_topic.values()) + [
    temperature_command_topic,
    pump1_command_topic,
    pump2_command_topic,
    ciculationmanual_command_topic,
    brightness_command_topic,
    light_command_topic,
    effect_command_topic,
]

# ── Read-only state the decoder produces ──
# Each row: (key, HA component, entity name, discovery extras, getter)
# The getter is called with the spa object once per update cycle.
def _clock(spa):
    if spa.time_hour < 0 or spa.time_minute < 0:
        return None
    return "{:02d}:{:02d}".format(spa.time_hour, spa.time_minute)


def _spa_date(spa):
    if spa.year < 0 or spa.month < 0 or spa.day < 0:
        return None
    return "{:04d}-{:02d}-{:02d}".format(spa.year, spa.month, spa.day)


DIAG = {'entity_category': 'diagnostic'}
DIAG_OFF = {'entity_category': 'diagnostic', 'enabled_by_default': False}

EXTRA_SENSORS = [
    # key                 component        friendly name            discovery extras                                            getter
    ('spa_clock',         'sensor',        'Spa Clock',             {'icon': 'mdi:clock-outline'},                               _clock),
    ('spa_date',          'sensor',        'Spa Date',              {'icon': 'mdi:calendar'},                                    _spa_date),
    ('heat_mode',         'sensor',        'Heat Mode',             {'icon': 'mdi:thermostat'},                                  lambda s: s.heatModeText),
    ('temp2',             'sensor',        'Water Temp 2',          {'device_class': 'temperature', 'unit_of_measurement': '°F',
                                                                     'state_class': 'measurement'},                              lambda s: s.temp2Valid),
    ('circ_mode',         'sensor',        'Circulation Mode',      {'icon': 'mdi:pump'},                                        lambda s: s.circMode),
    ('circ_auto',         'binary_sensor', 'Auto Circulation',      {'icon': 'mdi:autorenew'},                                   lambda s: s.autoCirc),
    ('circ_manual',       'binary_sensor', 'Manual Circulation',    {'icon': 'mdi:hand-water'},                                  lambda s: s.manualCirc),
    # On while circulation runs for heat, a filter cycle or the jets: the manual switch
    # cannot change it then (DECODING.md 3.1), and the bridge refuses the command.
    ('circ_locked',       'binary_sensor', 'Circulation Locked',    {'icon': 'mdi:lock-outline'},                                lambda s: s.circLocked),
    ('command_status',    'sensor',        'Last Command',          {'icon': 'mdi:message-alert-outline'},                       lambda s: s.commandStatus[:255]),
    ('light_cycle_time',  'sensor',        'Light Cycle Time',      dict(DIAG, icon='mdi:timer-outline'),                        lambda s: s.lightCycleTime),
    # Raw undecoded fields. Off by default - they exist so the long-baseline questions in
    # DECODING.md 6.2 can be answered from the HA recorder instead of a bespoke capture.
    ('raw_f3',            'sensor',        'Raw Field 3',           dict(DIAG_OFF, icon='mdi:help-box'),                         lambda s: s.UnknownField3),
    ('raw_f9',            'sensor',        'Raw Field 9',           dict(DIAG_OFF, icon='mdi:help-box'),                         lambda s: s.UnknownField9),
    ('raw_f12',           'sensor',        'Raw Field 12',          dict(DIAG_OFF, icon='mdi:help-box'),                         lambda s: s.UnknownField12),
    ('raw_display',       'sensor',        'Raw Display Field',     dict(DIAG_OFF, icon='mdi:help-box'),                         lambda s: s.displayText),
    ('raw_edit',          'sensor',        'Raw Edit Field',        dict(DIAG_OFF, icon='mdi:help-box'),                         lambda s: s.editField),
    ('panel_screen',      'sensor',        'Panel Screen',          dict(DIAG, icon='mdi:monitor'),                              lambda s: s.displayTextS),
    ('circ_bit5',         'binary_sensor', 'Circ Bit 5',            dict(DIAG_OFF, icon='mdi:help-box'),                         lambda s: s.circB5),
    ('circ_bit6',         'binary_sensor', 'Circ Bit 6',            dict(DIAG_OFF, icon='mdi:help-box'),                         lambda s: s.circB6),
]

extra_state_topic = {k: '{}/{}_state'.format(flora_name, k) for k, _, _, _, _ in EXTRA_SENSORS}

# Entities that were a misreading, removed by publishing an empty retained discovery config.
# "Panel Alert" and "Alert Day Count" were bit 7 and the low bits of the display field, which is
# a panel state XOR the UV days: they were the UV countdown, not an alert (DECODING.md 3.12).
RETIRED_DISCOVERY = [
    'homeassistant/binary_sensor/{}/panel_alert/config'.format(flora_name),
    'homeassistant/sensor/{}/alert_days/config'.format(flora_name),
]

modes = ["heat"];

deviceInfo = {
            'identifiers' : ["HotTub"],
            'manufacturer' : 'Sundance',
            'name' : flora_name,
    }




def on_connect(mqttc, obj, flags, rc):
    # rc != 0 is a REFUSED connection (4 = bad user/password, 5 = not authorised). Printing
    # "Connected" for it made a wrong password look healthy in the log.
    if rc == 0:
        print("Connected to MQTT.")
        # Subscribe on EVERY connect, not once at start-up. The session is clean (paho's
        # default), so the broker forgets our subscriptions whenever the connection drops;
        # paho reconnects by itself and calls this again, but a one-off subscribe is never
        # repeated. The bridge then keeps publishing state while receiving no commands.
        mqttc.subscribe([(topic, 0) for topic in command_topics])
    else:
        print("MQTT connection refused: %s (rc=%d)" % (mqtt.connack_string(rc), rc))


def on_message(mqttc, obj, msg):
    print(
        "MQTT message received on topic: "
        + msg.topic
        + " with value: "
        + msg.payload.decode()
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop = asyncio.get_event_loop()
    if msg.topic in (spa_time_command_topic, spa_date_command_topic, sync_clock_command_topic) \
            or msg.topic in service_command_topic.values():
        _menu_command(loop, msg.topic, msg.payload.decode().strip())
    elif msg.topic == temperature_command_topic:
        loop.run_until_complete(spa.send_temp_change(float(msg.payload.decode())))
    elif msg.topic == pump1_command_topic:
        loop.run_until_complete(spa.change_pump(0, float(msg.payload.decode())))
    elif msg.topic == pump2_command_topic:
        loop.run_until_complete(spa.change_pump(1, float(msg.payload.decode())))
    elif msg.topic == ciculationmanual_command_topic:
        loop.run_until_complete(spa.change_pump(2, float(msg.payload.decode())))
    elif msg.topic == brightness_command_topic:
        command = float(msg.payload.decode())
        # if command == 0:
            # #setting brigthness to 0 will turn off the lights and set the mode back to 0
            # res = "White"
            # for x,y in spa.LIGHT_MODE_MAP:
                # if x == spa.lightMode:
                   # res = y 
                   # break
            # spa.lastRGBMode = res 
        loop.run_until_complete(spa.change_rgbbrightness(0, command))
    elif msg.topic == light_command_topic:
        command = float(msg.payload.decode())
        if command == 0:
            # res = "White"
            # for x,y in spa.LIGHT_MODE_MAP:
                # if x == spa.lightMode:
                   # res = y 
                   # break
            # spa.lastRGBMode = res
            # spa.lastBrightness = spa.brigthness
            loop.run_until_complete(spa.change_rgbbrightness(0, 0))
        else:
            if spa.lightBrightnes == 0:
                loop.run_until_complete(spa.change_rgbbrightness(0, 100))
            # if spa.lastBrightness > 0:
                # loop.run_until_complete(spa.change_rgbbrightness(0, spa.lastBrightness))
            # else:

            # if spa.lastRGBMode == "Off":
                # loop.run_until_complete(spa.change_rgbmode(0, "White"))
            # else:
                # loop.run_until_complete(spa.change_rgbmode(0, spa.lastRGBMode))
    elif msg.topic == effect_command_topic:
        command = msg.payload.decode()
        if command == "Off":
            # res = "White"
            # for x,y in spa.LIGHT_MODE_MAP:
                # if x == spa.lightMode:
                   # res = y 
                   # break
            # spa.lastRGBMode = res
            # spa.lastBrightness = spa.brigthness
            loop.run_until_complete(spa.change_rgbbrightness(0, 0))
        else:
            loop.run_until_complete(spa.change_rgbmode(0, command))
    else:
        print("No logic for this topic, discarding.")
            
            
def _menu_refused(why):
    spa.menuStatus = "refused: " + why
    print("Panel menu: " + spa.menuStatus)


def _menu_command(loop, topic, payload):
    """Clock, date and service-timer commands. They become panel-menu jobs (panel_menu.py)."""
    try:
        if topic == spa_time_command_topic:
            m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", payload)
            if not m:
                return _menu_refused("%r is not HH:MM" % payload)
            loop.run_until_complete(spa.set_time(int(m.group(1)), int(m.group(2))))
        elif topic == spa_date_command_topic:
            try:
                d = datetime.strptime(payload, "%Y-%m-%d")
            except ValueError:
                return _menu_refused("%r is not YYYY-MM-DD" % payload)
            loop.run_until_complete(spa.set_date(d.year, d.month, d.day))
        elif topic == sync_clock_command_topic:
            if not spa_tz or ZoneInfo is None:
                return _menu_refused("set SPA_TZ (e.g. Europe/London) to sync the clock")
            now = datetime.now(ZoneInfo(spa_tz))
            if (spa.year, spa.month, spa.day) != (now.year, now.month, now.day):
                loop.run_until_complete(spa.set_date(now.year, now.month, now.day))
            # The presses take a while; aim at the minute the time edit will be reached in.
            loop.run_until_complete(spa.set_time(now.hour, now.minute))
        else:
            menu = [m for k, _, _, _, m in SERVICE_TIMERS if service_command_topic[k] == topic][0]
            loop.run_until_complete(spa.set_service_days(menu, int(float(payload))))
    except Exception as e:      # ValueError for an impossible date, mostly
        return _menu_refused(str(e))


last_menu_status = None
last_command_status = None


async def read_spa_data(spa, lastupd):
    await asyncio.sleep(0.1)
    if spa.lastupd != lastupd:
        lastupd = spa.lastupd
        #print(
        #    "New data as of "
        #    + datetime.utcfromtimestamp(spa.lastupd).strftime("%d-%m-%Y %H:%M:%S")
        #)

        data = OrderedDict()

        #print("CRC Errors: {0}".format(spa.crcerror))
        #print("Dropped Bytes: {0}".format(spa.dropped))
        #print("Last Command Attempts: {0}".format(spa.attemptsToCommand))
        
        #print("Current Temp: {0}".format(spa.curtemp))
        #print("Current Temp2: {0}".format(spa.temp2))
        #print("Set Temp: {0}".format(spa.get_settemp()))          
        #print("Target Temp: {0}".format(spa.targetTemp))  
        #print("Heat State: {1} {0}".format(spa.get_heatstate(True),spa.heatState2))
        #print("Pump Status: {0}".format(str(spa.pump_status)))
        #print("Pump Target Status: {0}".format(str(spa.target_pump_status)))
        #print("Circulation Pump: {0}  Auto:  {1}  Manual: {2}  Unkfield: {3}".format(spa.get_circ_pump(True), spa.autoCirc, spa.manualCirc, spa.unknownCirc))

        res = "unknown"
        for x,y in spa.DISPLAY_MAP:
            if x == spa.get_displayText():
               res = y 
               break
        
        #print("Display Text: {} {} ".format(spa.get_displayText(),res))
        
        
        res = "unknown"
        for x,y in spa.HEAT_MODE_MAP:
            if x == spa.get_heatMode():
               res = y 
               break
        
        #print("Heat Mode: {} {} ".format(spa.get_heatMode(), res))
        
        #print("UnknownField3: {}".format(spa.UnknownField3))
        #print("UnknownField9: {}".format(spa.UnknownField9))

        lightModeText = "unknown"
        for x,y in spa.LIGHT_MODE_MAP:
            if x == spa.lightMode:
               lightModeText = y 
               break

        #print("Light Status: Mode: {0} {6} Brightness: {1} Time: {5} Colors: R: {2} G: {3} B: {4} ".format(spa.lightMode, spa.lightBrightnes, spa.lightR, spa.lightG, spa.lightB, spa.lightCycleTime, lightModeText))

        res = "unknown"
        for x,y in spa.LIGHT_MODE_MAP:
            if x == spa.targetlightMode:
               res = y 
               break
        

        #print("Target Light Status: Mode: {0} {2} Brightness: {1} ".format(spa.targetlightMode, spa.targetlightBrightnes,  res))  

        #print("Spa Time: Y{0:04d} M{1:02d} D{2:02d} {3:02d}:{4:02d} {5}".format(
        #    spa.year,
        #    spa.month,
        #    spa.day,
        #    spa.time_hour,
        #    spa.time_minute,
        #    spa.get_timescale(True)
        #))
        
        mqtt_client.publish(current_temperature_topic, spa.curtemp, retain=True)      
        mqtt_client.publish(temperature_state_topic, spa.get_settemp(), retain=True) 
        mqtt_client.publish(action_topic, spa.get_heatstate(True), retain=True) 
        mqtt_client.publish(pump1_state_topic, spa.pump_status[0], retain=True) 
        mqtt_client.publish(pump2_state_topic, spa.pump_status[1], retain=True) 
        mqtt_client.publish(ciculationpump_state_topic, spa.get_circ_pump(), retain=True) 
        mqtt_client.publish(ciculationmanual_state_topic, spa.manualCirc, retain=True)  # the switch sends ClearRay, so it must report MANUAL circulation, not the circ pump
        
        mqtt_client.publish(brightness_state_topic, spa.lightBrightnes, retain=True)  
        mqtt_client.publish(rgb_state_topic, "{},{},{}".format(spa.lightR,spa.lightG,spa.lightB,), retain=True)  
        mqtt_client.publish(effect_state_topic, lightModeText, retain=True)  
        
        for key, component, _name, extras, getter in EXTRA_SENSORS:
            try:
                value = getter(spa)
            except AttributeError:
                # A field the decoder has not populated yet (nothing received since start).
                continue
            if value is None:
                continue
            if component == 'binary_sensor' and value < 0:
                continue  # -1 is the decoder's "never seen", not a state
            mqtt_client.publish(extra_state_topic[key], value, retain=True)

        for key, _name, top, attr, _menu in SERVICE_TIMERS:
            value = getattr(spa, attr, -1)
            if 0 <= value <= top:      # outside the range means the decode is wrong, not the tub
                mqtt_client.publish(service_state_topic[key], value, retain=True)

        if(spa.lightMode > 0):
            mqtt_client.publish(light_state_topic, 1, retain=True)   
        else:
            mqtt_client.publish(light_state_topic, 0, retain=True)   
 
    # A refusal happens on a command, not on a frame, so publish it the moment it changes.
    global last_command_status
    if spa.commandStatus != last_command_status:
        last_command_status = spa.commandStatus
        mqtt_client.publish(extra_state_topic['command_status'], spa.commandStatus[:255], retain=True)

    global last_menu_status
    if spa.menuStatus != last_menu_status:
        last_menu_status = spa.menuStatus
        mqtt_client.publish(menu_status_topic, spa.menuStatus[:255], retain=True)

    if spa.connected:     
        mqtt_client.publish(availability_topic, "online", retain=True) 
    else:
        mqtt_client.publish(availability_topic, "offline", retain=True) 
    
    return lastupd


async def start_mqtt(spa):
    global mqtt_client
    mqtt_client = mqtt.Client("jacuzzi_rs485")
    if mqtt_user:
        mqtt_client.username_pw_set(mqtt_user, mqtt_password or None)
    if mqtt_tls:
        mqtt_client.tls_set(ca_certs=mqtt_ca_file)    # None = system trust store; hostname checked
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(mqtt_host, mqtt_port)
    mqtt_client.loop_start()



    discovery_topic = 'homeassistant/climate/{}/{}/config'.format(flora_name.lower(), "temperature")
    payload = OrderedDict()
    payload['name'] = "Temperature"
    payload['has_entity_name'] = True
    payload['unique_id'] = "{}-{}".format(flora_name,"climate")
    payload['value_template'] = "{{value}}"
    payload['current_temperature_topic'] = current_temperature_topic
    payload['current_temperature_template'] = "{{value}}"
    payload['temperature_command_topic'] = temperature_command_topic
    payload['temperature_state_topic'] = temperature_state_topic
    payload['temperature_state_template'] = "{{value}}"
    payload['mode_state_topic'] = mode_state_topic
    payload['action_topic'] = action_topic 
    payload['availability_topic'] = availability_topic
    payload['icon'] = "mdi:hot-tub"
    payload['modes'] = modes
    payload['temp_step'] = "1"
    payload['precision'] = 1
    payload['temperature_unit'] = "F"
    payload['min_temp'] = "60"
    payload['max_temp'] = "104"
    payload['device'] = deviceInfo
    payload['expire_after'] = str(int(300 * 1.5))
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, True)
    
    
    
    discovery_topic = 'homeassistant/switch/{}/{}/config'.format(flora_name.lower(), "Pump1")
    payload = OrderedDict()
    payload['name'] = "Pump 1"
    payload['has_entity_name'] = True
    payload['unique_id'] = "{}-{}".format(flora_name,"Pump1")
    payload['value_template'] = "{{value}}"
    payload['state_topic'] = pump1_state_topic
    payload['state_on'] = 1
    payload['payload_on'] = 1
    payload['state_off'] = 0
    payload['payload_off'] = 0
    payload['command_topic'] = pump1_command_topic
    payload['availability_topic'] = availability_topic
    payload['device'] = deviceInfo
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, True)

    discovery_topic = 'homeassistant/switch/{}/{}/config'.format(flora_name.lower(), "Pump2")
    payload = OrderedDict()
    payload['name'] = "Pump 2"
    payload['has_entity_name'] = True
    payload['unique_id'] = "{}-{}".format(flora_name,"Pump2")
    payload['value_template'] = "{{value}}"
    payload['state_topic'] = pump2_state_topic
    payload['state_on'] = 1
    payload['payload_on'] = 1
    payload['state_off'] = 0
    payload['payload_off'] = 0
    payload['command_topic'] = pump2_command_topic
    payload['availability_topic'] = availability_topic
    payload['device'] = deviceInfo
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, True)


    discovery_topic = 'homeassistant/switch/{}/{}/config'.format(flora_name.lower(), "ManualCiculationPump")
    payload = OrderedDict()
    payload['name'] = "Manual Circ Pump"
    payload['has_entity_name'] = True
    payload['unique_id'] = "{}-{}".format(flora_name,"ManCircPump")
    payload['value_template'] = "{{value}}"
    payload['state_topic'] = ciculationmanual_state_topic
    payload['state_on'] = 1
    payload['payload_on'] = 1
    payload['state_off'] = 0
    payload['payload_off'] = 0
    payload['command_topic'] = ciculationmanual_command_topic
    payload['availability_topic'] = availability_topic
    payload['device'] = deviceInfo
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, True)

    discovery_topic = 'homeassistant/binary_sensor/{}/{}/config'.format(flora_name.lower(), "CiculationPump")
    payload = OrderedDict()
    payload['name'] = "Circulation Pump"
    payload['has_entity_name'] = True
    payload['unique_id'] = "{}-{}".format(flora_name,"CircPump")
    payload['value_template'] = "{{value}}"
    payload['state_topic'] = ciculationpump_state_topic
    payload['state_on'] = 1
    payload['payload_on'] = 1
    payload['state_off'] = 0
    payload['payload_off'] = 0
    payload['availability_topic'] = availability_topic
    payload['device'] = deviceInfo
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, True)


    lightModes = []
    for x,y in spa.LIGHT_MODE_MAP:
        lightModes.append(y)

    discovery_topic = 'homeassistant/light/{}/{}/config'.format(flora_name.lower(), "Lights")
    payload = OrderedDict()
    payload['name'] = "Lights"
    payload['has_entity_name'] = True
    payload['unique_id'] = "{}-{}".format(flora_name,"Lights")

    payload['brightness_command_topic'] = brightness_command_topic 
    payload['brightness_scale'] = 100
    payload['brightness_state_topic'] = brightness_state_topic
    payload['command_topic'] = light_command_topic
    payload['state_topic'] = light_state_topic 
    #payload['rgb_state_topic'] = rgb_state_topic 

    payload['effect_list'] = lightModes
    payload['effect_state_topic'] = effect_state_topic
    payload['effect_command_topic'] = effect_command_topic 
    
    payload['payload_on'] = 1
    payload['payload_off'] = 0
    payload['availability_topic'] = availability_topic
    payload['device'] = deviceInfo
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, True)


    for key, component, name, extras, _getter in EXTRA_SENSORS:
        discovery_topic = 'homeassistant/{}/{}/{}/config'.format(component, flora_name.lower(), key)
        payload = OrderedDict()
        # has_entity_name lets HA compose "<device> <entity>" itself. Without it, passing
        # the device name in `name` gets the device name prefixed a SECOND time, giving
        # entities called "hottub hottub Spa Clock" and ids like
        # sensor.hottub_hottub_spa_clock. Entity ids stick by unique_id once created, so
        # fixing this in the payload does NOT rename entities that already exist; those
        # have to be renamed once in the entity registry.
        payload['name'] = name
        payload['has_entity_name'] = True
        payload['unique_id'] = "{}-{}".format(flora_name, key)
        payload['state_topic'] = extra_state_topic[key]
        if component == 'binary_sensor':
            payload['payload_on'] = 1
            payload['payload_off'] = 0
        payload['availability_topic'] = availability_topic
        payload['device'] = deviceInfo
        payload.update(extras)
        mqtt_client.publish(discovery_topic, json.dumps(payload), 1, True)

    # Controls that go through the panel menus (panel_menu.py). The text boxes show what the
    # spa reads now; typing a new value sets it. Progress and refusals: Panel Menu Status.
    for key, name, pattern, state, cmd, icon in (
            ('spa_time_set', 'Set Spa Time', r'^([01]?\d|2[0-3]):[0-5]\d$',
             extra_state_topic['spa_clock'], spa_time_command_topic, 'mdi:clock-edit-outline'),
            ('spa_date_set', 'Set Spa Date', r'^\d{4}-\d{2}-\d{2}$',
             extra_state_topic['spa_date'], spa_date_command_topic, 'mdi:calendar-edit')):
        payload = OrderedDict()
        payload['name'] = name
        payload['has_entity_name'] = True
        payload['unique_id'] = "{}-{}".format(flora_name, key)
        payload['state_topic'] = state
        payload['command_topic'] = cmd
        payload['pattern'] = pattern
        payload['min'] = 4
        payload['max'] = 10
        payload['icon'] = icon
        payload['entity_category'] = 'config'
        payload['availability_topic'] = availability_topic
        payload['device'] = deviceInfo
        mqtt_client.publish('homeassistant/text/{}/{}/config'.format(flora_name.lower(), key),
                            json.dumps(payload), 1, True)

    payload = OrderedDict()
    payload['name'] = "Sync Spa Clock"
    payload['has_entity_name'] = True
    payload['unique_id'] = "{}-sync_clock".format(flora_name)
    payload['command_topic'] = sync_clock_command_topic
    payload['payload_press'] = "sync"
    payload['icon'] = 'mdi:clock-check-outline'
    payload['entity_category'] = 'config'
    payload['availability_topic'] = availability_topic
    payload['device'] = deviceInfo
    mqtt_client.publish('homeassistant/button/{}/sync_clock/config'.format(flora_name.lower()),
                        json.dumps(payload), 1, True)

    for key, name, top, attr, _menu in SERVICE_TIMERS:
        payload = OrderedDict()
        payload['name'] = name
        payload['has_entity_name'] = True
        payload['unique_id'] = "{}-{}".format(flora_name, key)
        payload['state_topic'] = service_state_topic[key]
        payload['command_topic'] = service_command_topic[key]
        payload['min'] = 0
        payload['max'] = top
        payload['step'] = 1
        payload['mode'] = 'box'
        payload['unit_of_measurement'] = 'd'
        payload['icon'] = 'mdi:calendar-clock'
        payload['entity_category'] = 'config'
        payload['availability_topic'] = availability_topic
        payload['device'] = deviceInfo
        mqtt_client.publish('homeassistant/number/{}/{}/config'.format(flora_name.lower(), key),
                            json.dumps(payload), 1, True)

    payload = OrderedDict()
    payload['name'] = "Panel Menu Status"
    payload['has_entity_name'] = True
    payload['unique_id'] = "{}-menu_status".format(flora_name)
    payload['state_topic'] = menu_status_topic
    payload['icon'] = 'mdi:gesture-tap-button'
    payload['entity_category'] = 'diagnostic'
    payload['availability_topic'] = availability_topic
    payload['device'] = deviceInfo
    mqtt_client.publish('homeassistant/sensor/{}/menu_status/config'.format(flora_name.lower()),
                        json.dumps(payload), 1, True)

    for topic in RETIRED_DISCOVERY:
        mqtt_client.publish(topic, '', 1, True)

    #Only support heat mode for now
    mqtt_client.publish(mode_state_topic, "heat", retain=True)
    # Command topics are subscribed in on_connect, so a reconnect restores them.


async def supervise_listen(spa):
    """Run spa.listen() forever, restarting it if it ever throws.

    spa.listen() is meant to loop indefinitely. If an unexpected exception
    escapes it, the task would die and we would silently stop reading data
    with no indication why. Restarting (and forcing a reconnect) keeps the
    bridge alive across transient failures.
    """
    while True:
        try:
            await spa.listen()
        except Exception as e:
            print("spa.listen() crashed: {}; restarting in 5s".format(e))
            spa._drop_link()
            await asyncio.sleep(5)


async def start_app():
    """Test a miniature engine of talking to the spa."""
    global spa

    print("v.3")

    # Connect to Spa (Serial Device)
    spa = sundanceRS485.SundanceRS485(serial_ip, serial_port)
    await spa.connect()

    # Connect to MQTT
    await start_mqtt(spa)

    asyncio.ensure_future(supervise_listen(spa))
    lastupd = 0

    while True:
        lastupd = await read_spa_data(spa, lastupd)


if __name__ == "__main__":
    asyncio.run(start_app())