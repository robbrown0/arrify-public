"""Conservative same-network eligibility for remote Lock Screen commands.

Use explicit local CIDRs. Forwarded addresses are accepted only from configured
trusted proxies; a proxy connection alone never proves the listener is local.
Observations are ephemeral and refreshed by successful owner heartbeats.
"""
import ipaddress
import os
import threading
import time


class LocalControls:
    def __init__(self, networks=None, proxies=None):
        def parse(value):
            return [ipaddress.ip_network(x.strip()) for x in value.split(',') if x.strip()]
        self.networks = parse(os.environ.get('ARRIFY_LOCK_SCREEN_NETWORKS', '') if networks is None else networks)
        self.proxies = parse(os.environ.get('ARRIFY_TRUSTED_PROXIES', '') if proxies is None else proxies)
        self.owners = {}; self.lock = threading.Lock()

    def address(self, handler):
        try:
            peer = ipaddress.ip_address(handler.client_address[0])
            forwarded = handler.headers.get('X-Forwarded-For', '')
            if any(peer in n for n in self.proxies):
                if not forwarded or len(forwarded) > 1024: return None
                chain = [ipaddress.ip_address(x.strip()) for x in forwarded.split(',')] + [peer]
                while chain and any(chain[-1] in n for n in self.proxies): chain.pop()
                return chain[-1] if chain else None
            # Do not mistake an unconfigured local reverse proxy for a local client.
            if forwarded or handler.headers.get('Forwarded'): return None
            return peer
        except ValueError: return None

    def observe(self, uid, data, state, handler):
        if not data or data.get('action') != 'heartbeat': return
        status = state.get('listening', state)
        owner = status.get('owner', status.get('owner_device_id'))
        if not owner or owner != data.get('device_id'): return
        epoch = status.get('epoch', status.get('owner_epoch'))
        address = self.address(handler)
        now = time.monotonic()
        with self.lock:
            self.owners = {key:value for key,value in self.owners.items() if now - value[2] < 45}
            self.owners[uid] = (owner, epoch, now, address)

    def allows(self, uid, owner, epoch, handler):
        address = self.address(handler)
        if address is None: return False
        with self.lock: previous = self.owners.get(uid)
        if not previous or previous[:2] != (owner, epoch) or time.monotonic() - previous[2] >= 45: return False
        other = previous[3]
        return other is not None and any(address in network and other in network for network in self.networks)
