import unittest
from unittest.mock import patch
from tools.sonos_relative_volume_probe import Client, ProbeError, State, direct, endpoint, level, local_path, one, probe, xml


class FakeClient:
    def __init__(self, states, responses=(15, 16)):
        self.states = iter(states)
        self.responses = iter(responses)
        self.writes = []

    def state(self):
        return next(self.states)

    def relative(self, delta):
        self.writes.append(delta)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


class SonosRelativeProbeTests(unittest.TestCase):
    def test_default_never_writes(self):
        client = FakeClient([State(16, 0, 'a')])
        self.assertEqual(probe(client)['outcome'], 'read-only')
        self.assertEqual(client.writes, [])

    def test_one_step_restores_only_matching_current_state(self):
        client = FakeClient([State(16, 0, 'a'), State(15, 0, 'a'), State(16, 0, 'a')])
        self.assertEqual(probe(client, True, lambda: None)['outcome'], 'restored')
        self.assertEqual(client.writes, [-1, 1])

    def test_external_volume_mute_or_group_change_prevents_restore(self):
        for changed in (State(14, 0, 'a'), State(15, 1, 'a'), State(15, 0, 'b')):
            with self.subTest(changed=changed):
                client = FakeClient([State(16, 0, 'a'), changed])
                self.assertIn('no restore', probe(client, True, lambda: None)['outcome'])
                self.assertEqual(client.writes, [-1])

    def test_unexpected_relative_result_prevents_restore(self):
        client = FakeClient([State(16, 0, 'a'), State(15, 0, 'a')], responses=(80,))
        self.assertIn('no restore', probe(client, True, lambda: None)['outcome'])
        self.assertEqual(client.writes, [-1])

    def test_ambiguous_write_is_never_replayed(self):
        client = FakeClient([State(16, 0, 'a')], responses=(ProbeError('timeout'),))
        with self.assertRaises(ProbeError):
            probe(client, True, lambda: None)
        self.assertEqual(client.writes, [-1])

    def test_ambiguous_restore_is_never_replayed(self):
        client = FakeClient([State(16, 0, 'a'), State(15, 0, 'a')], responses=(15, ProbeError('timeout')))
        with self.assertRaises(ProbeError):
            probe(client, True, lambda: None)
        self.assertEqual(client.writes, [-1, 1])

    def test_unmuted_nonboundary_level_required(self):
        for state in (State(0, 0, 'a'), State(100, 0, 'a'), State(16, 1, 'a')):
            with self.subTest(state=state):
                client = FakeClient([state])
                with self.assertRaises(ProbeError):
                    probe(client, True, lambda: None)
                self.assertEqual(client.writes, [])

    def test_final_readback_not_assumed_from_success(self):
        client = FakeClient([State(16, 0, 'a'), State(15, 0, 'a'), State(17, 0, 'a')])
        self.assertIn('differs', probe(client, True, lambda: None)['outcome'])
        self.assertEqual(client.writes, [-1, 1])

    def test_strict_levels_do_not_invent_or_clamp_values(self):
        self.assertEqual(level(' 16 '), 16)
        for invalid in ('', 'NaN', '16.0', '-1', '101', '999999'):
            with self.subTest(invalid=invalid), self.assertRaises(ProbeError):
                level(invalid)

    def test_only_private_literal_device_endpoint(self):
        self.assertEqual(endpoint('http://192.168.1.2:1400/xml/device_description.xml'), ('192.168.1.2', 1400))
        for invalid in ('http://example.com:1400/xml/device_description.xml',
                        'http://127.0.0.1:1400/xml/device_description.xml',
                        'http://8.8.8.8:1400/xml/device_description.xml',
                        'http://192.168.1.2:8060/xml/device_description.xml',
                        'http://user:secret@192.168.1.2:1400/xml/device_description.xml'):
            with self.subTest(invalid=invalid), self.assertRaises(ProbeError):
                endpoint(invalid)

    def test_service_cannot_redirect_or_inject_path(self):
        self.assertEqual(local_path('/MediaRenderer/GroupRenderingControl/Control', '192.168.1.2'), '/MediaRenderer/GroupRenderingControl/Control')
        for invalid in ('http://192.168.1.3:1400/control', '//example.com/control', '/control?other=1', '/../control', '/control\r\nInjected: yes'):
            with self.subTest(invalid=invalid), self.assertRaises(ProbeError):
                local_path(invalid, '192.168.1.2')

    def test_ambiguous_or_entity_response_rejected(self):
        with self.assertRaises(ProbeError):
            one(xml(b'<root><NewVolume>15</NewVolume><NewVolume>80</NewVolume></root>'), 'NewVolume')
        with self.assertRaises(ProbeError):
            xml(b'<!DOCTYPE r [<!ENTITY v "80">]><r>&v;</r>')
        with self.assertRaises(ProbeError):
            xml(b'x' * 131073)

    def test_preflight_selects_primary_identity_and_reads_nested_renderer_services(self):
        discovery = {'devices': [{'location': 'http://192.168.1.2:1400/xml/device_description.xml',
                                 'model': 'Fixture', 'room': 'Fixture', 'udn': 'uuid:fixture'}]}
        capabilities = {'primary_location': discovery['devices'][0]['location'], 'primary_model': 'Fixture', 'room': 'Fixture'}
        description = b'''<root><device><UDN>uuid:fixture</UDN><serviceList><service>
        <serviceType>urn:schemas-upnp-org:service:ZoneGroupTopology:1</serviceType>
        <SCPDURL>/topology.xml</SCPDURL><controlURL>/topology</controlURL></service></serviceList>
        <deviceList><device><UDN>uuid:fixture_MR</UDN><serviceList><service>
        <serviceType>urn:schemas-upnp-org:service:GroupRenderingControl:1</serviceType>
        <SCPDURL>/group.xml</SCPDURL><controlURL>/group</controlURL></service></serviceList>
        </device></deviceList></device></root>'''
        # Argument names must not be mistaken for additional action names.
        scpd = b'''<scpd><actionList><action><name>GetGroupVolume</name><argumentList>
        <argument><name>InstanceID</name></argument></argumentList></action>
        <action><name>GetGroupMute</name></action><action><name>SetRelativeGroupVolume</name>
        <argumentList><argument><name>Adjustment</name></argument></argumentList></action></actionList></scpd>'''
        with patch.object(Client, 'request', side_effect=[description, scpd]) as request:
            client = Client(discovery, capabilities)
        self.assertEqual(client.uuid, 'fixture')
        self.assertEqual([call.args[0] for call in request.call_args_list], ['GET', 'GET'])
        self.assertEqual(client.audit, [])
        with patch.object(Client, 'request', return_value=description.replace(b'uuid:fixture</UDN>', b'uuid:other</UDN>')):
            with self.assertRaises(ProbeError):
                Client(discovery, capabilities)

    def test_no_arbitrary_or_large_volume_action(self):
        client = object.__new__(Client)
        for action, adjustment in (('SetVolume', 80), ('SetGroupMute', 1), ('SetRelativeGroupVolume', 2), ('SetRelativeGroupVolume', None)):
            with self.subTest(action=action, adjustment=adjustment), self.assertRaises(ProbeError):
                client.rpc(action, adjustment)

    def test_ambiguous_rpc_attempt_is_audited_without_retry(self):
        client = object.__new__(Client)
        client.audit = []
        client.services = {'urn:schemas-upnp-org:service:GroupRenderingControl:1': {'controlURL': '/group'}}
        with patch.object(client, 'request', side_effect=ProbeError('ambiguous')) as request:
            with self.assertRaises(ProbeError):
                client.rpc('SetRelativeGroupVolume', -1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(client.audit[0]['action'], 'SetRelativeGroupVolume')
        self.assertEqual(client.audit[0]['adjustment'], -1)
        self.assertEqual(client.audit[0]['outcome'], 'failed-or-ambiguous')


if __name__ == '__main__':
    unittest.main()
