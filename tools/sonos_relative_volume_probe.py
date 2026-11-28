#!/usr/bin/env python3
"""Bounded operator probe, not an Arrify runtime integration.

Default is read-only. --probe-one-step sends exactly one relative Down, and
restores one point only after matching volume, mute and single-room topology.
Never retries an ambiguous write. Input discovery data stays outside the repo.
"""
import argparse
from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

GROUP = 'urn:schemas-upnp-org:service:GroupRenderingControl:1'
TOPOLOGY = 'urn:schemas-upnp-org:service:ZoneGroupTopology:1'
LIMIT = 131072


class ProbeError(Exception):
    pass


def xml(data):
    if len(data) > LIMIT or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ProbeError('Unsafe XML response')
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise ProbeError('Invalid XML response') from error


def name(element):
    return element.tag.rsplit('}', 1)[-1]


def one(root, field):
    values = [node.text or '' for node in root.iter() if name(node) == field]
    if len(values) != 1:
        raise ProbeError('Missing or ambiguous response field: ' + field)
    return values[0]


def direct(root, field):
    values = [node.text or '' for node in root if name(node) == field]
    if len(values) != 1:
        raise ProbeError('Missing or ambiguous direct field: ' + field)
    return values[0]


def level(value, maximum=100):
    text = value.strip()
    if not re.fullmatch(r'[0-9]{1,3}', text) or not 0 <= int(text) <= maximum:
        raise ProbeError('Invalid numeric readback')
    return int(text)


def endpoint(url):
    parts = urlsplit(url)
    try:
        address = ipaddress.IPv4Address(parts.hostname or '')
        port = parts.port
    except ValueError as error:
        raise ProbeError('Invalid device endpoint') from error
    networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
    if (parts.scheme != 'http' or port != 1400 or parts.username or parts.password
            or parts.query or parts.fragment or not any(address in ipaddress.IPv4Network(n) for n in networks)
            or parts.path != '/xml/device_description.xml'):
        raise ProbeError('Unexpected device endpoint')
    return str(address), port


def local_path(value, host):
    parts = urlsplit(value)
    try:
        foreign = parts.netloc and (parts.scheme != 'http' or parts.hostname != host or parts.port != 1400)
    except ValueError as error:
        raise ProbeError('Invalid service endpoint') from error
    if foreign or (parts.scheme and not parts.netloc):
        raise ProbeError('Service points at another endpoint')
    if (parts.query or parts.fragment or not parts.path.startswith('/')
            or any(c in value for c in '\r\n') or '..' in parts.path.split('/')):
        raise ProbeError('Invalid service path')
    return parts.path


@dataclass(frozen=True)
class State:
    volume: int
    mute: int
    topology: str


def probe(client, live=False, settle=lambda: time.sleep(.25)):
    before = client.state()
    report = {'before': before.__dict__, 'live': live, 'relative_commands': 0}
    if not live:
        report['outcome'] = 'read-only'
        return report
    if before.mute or not 1 <= before.volume <= 99:
        raise ProbeError('Probe requires an unmuted, nonboundary existing level')
    # A failure after sending either write is ambiguous. Never replay it or
    # manufacture a rollback from the remembered starting level.
    down = client.relative(-1)
    report['relative_commands'] += 1
    settle()
    after = client.state()
    report['after_down'] = after.__dict__
    if down != before.volume - 1 or after != State(before.volume - 1, before.mute, before.topology):
        report['outcome'] = 'stopped: readback changed; no restore attempted'
        return report
    restored = client.relative(1)
    report['relative_commands'] += 1
    settle()
    final = client.state()
    report['final'] = final.__dict__
    report['outcome'] = 'restored' if restored == before.volume and final == before else 'stopped: final readback differs'
    return report


class Client:
    def __init__(self, discovery, capabilities):
        matches = [d for d in discovery['devices'] if d['location'] == capabilities['primary_location']
                   and d['model'] == capabilities['primary_model']
                   and d['room'].casefold() == capabilities['room'].casefold()]
        if len(matches) != 1:
            raise ProbeError('Expected one previously verified primary device')
        target = matches[0]
        self.host, self.port = endpoint(target['location'])
        self.uuid = target['udn'].removeprefix('uuid:')
        self.audit = []
        description = xml(self.request('GET', '/xml/device_description.xml'))
        devices = [node for node in description if name(node) == 'device']
        if len(devices) != 1 or direct(devices[0], 'UDN').removeprefix('uuid:') != self.uuid:
            raise ProbeError('Device identity changed')
        services = {}
        for item in description.iter():
            if name(item) == 'service':
                fields = {name(c): c.text or '' for c in item}
                kind = fields.get('serviceType')
                if kind in (GROUP, TOPOLOGY):
                    if kind in services:
                        raise ProbeError('Ambiguous device service')
                    services[kind] = {k: local_path(fields[k], self.host) for k in ('controlURL', 'SCPDURL')}
        if set(services) != {GROUP, TOPOLOGY}:
            raise ProbeError('Required services unavailable')
        self.services = services
        scpd = xml(self.request('GET', services[GROUP]['SCPDURL']))
        actions = {direct(a, 'name') for a in scpd.iter() if name(a) == 'action'}
        if not {'GetGroupVolume', 'GetGroupMute', 'SetRelativeGroupVolume'} <= actions:
            raise ProbeError('Relative group control not advertised')

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2.5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read(LIMIT + 1)
            if response.status != 200 or len(data) > LIMIT:
                raise ProbeError('Device request did not produce a bounded success response')
            return data
        except (OSError, http.client.HTTPException) as error:
            # Avoid exporting LAN addresses through exception strings.
            raise ProbeError('Device request failed; any submitted write is ambiguous') from error
        finally:
            connection.close()

    def rpc(self, action, adjustment=None):
        allowed = {'GetGroupVolume', 'GetGroupMute', 'GetZoneGroupState', 'SetRelativeGroupVolume'}
        if action not in allowed or (action == 'SetRelativeGroupVolume' and adjustment not in (-1, 1)):
            raise ProbeError('Probe action is not allowed')
        kind = TOPOLOGY if action == 'GetZoneGroupState' else GROUP
        envelope = ET.Element('{http://schemas.xmlsoap.org/soap/envelope/}Envelope')
        body = ET.SubElement(envelope, '{http://schemas.xmlsoap.org/soap/envelope/}Body')
        command = ET.SubElement(body, '{' + kind + '}' + action)
        if kind == GROUP:
            ET.SubElement(command, 'InstanceID').text = '0'
        if action == 'SetRelativeGroupVolume':
            ET.SubElement(command, 'Adjustment').text = str(adjustment)
        started = time.perf_counter()
        audit = {'action': action, 'outcome': 'failed-or-ambiguous'}
        if action == 'SetRelativeGroupVolume':
            audit['adjustment'] = adjustment
        self.audit.append(audit)
        try:
            data = self.request('POST', self.services[kind]['controlURL'], ET.tostring(envelope),
                                {'Content-Type': 'text/xml; charset="utf-8"', 'SOAPACTION': '"' + kind + '#' + action + '"'})
            result = xml(data)
            if any(name(n) == 'Fault' for n in result.iter()):
                raise ProbeError('Device reported a SOAP fault')
            expected = '{' + kind + '}' + action + 'Response'
            if len([n for n in result.iter() if n.tag == expected]) != 1:
                raise ProbeError('Unexpected SOAP response')
            audit['outcome'] = 'response-validated'
            return result
        finally:
            audit['milliseconds'] = round((time.perf_counter()-started)*1000, 3)

    def state(self):
        volume = level(one(self.rpc('GetGroupVolume'), 'CurrentVolume'))
        mute = level(one(self.rpc('GetGroupMute'), 'CurrentMute'), maximum=1)
        topology = xml(one(self.rpc('GetZoneGroupState'), 'ZoneGroupState').encode())
        groups = [n for n in topology.iter() if name(n) == 'ZoneGroup' and n.get('Coordinator') == self.uuid]
        if len(groups) != 1:
            raise ProbeError('Verified primary is no longer group coordinator')
        members = [(n.get('UUID'), '1' if name(n) == 'Satellite' else n.get('Invisible', '0')) for n in groups[0].iter()
                   if name(n) in ('ZoneGroupMember', 'Satellite')]
        if any(not uid or invisible not in ('0', '1') for uid, invisible in members):
            raise ProbeError('Invalid topology identity')
        visible = [uid for uid, invisible in members if invisible != '1']
        if visible != [self.uuid]:
            raise ProbeError('Probe is limited to the verified single room and its bonded satellites')
        fingerprint = hashlib.sha256(json.dumps([groups[0].attrib, sorted(members)], sort_keys=True).encode()).hexdigest()
        return State(volume, mute, fingerprint)

    def relative(self, delta):
        return level(one(self.rpc('SetRelativeGroupVolume', delta), 'NewVolume'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discovery', type=Path, required=True)
    parser.add_argument('--capabilities', type=Path, required=True)
    parser.add_argument('--probe-one-step', action='store_true')
    args = parser.parse_args()
    client = None
    try:
        client = Client(json.loads(args.discovery.read_text()), json.loads(args.capabilities.read_text()))
        result = probe(client, live=args.probe_one_step)
    except ProbeError as error:
        result = {'outcome': 'stopped', 'reason': str(error), 'no_write_retry': True}
    result['requests'] = client.audit if client else []
    print(json.dumps(result, indent=2))
    return 0 if result['outcome'] in ('read-only', 'restored') else 1


if __name__ == '__main__':
    raise SystemExit(main())
