"""Small per-account invalidations; SQLite listening state remains authoritative."""
import threading

class ListeningEvents:
    def __init__(self):self.condition=threading.Condition();self.versions={};self.clients={}
    def publish(self,uid):
        with self.condition:self.versions[uid]=self.versions.get(uid,0)+1;self.condition.notify_all()
    def open(self,uid):
        with self.condition:
            if self.clients.get(uid,0)>=4:return False
            self.clients[uid]=self.clients.get(uid,0)+1;return True
    def close(self,uid):
        with self.condition:
            left=max(0,self.clients.get(uid,0)-1)
            if left:self.clients[uid]=left
            else:self.clients.pop(uid,None)
    def version(self,uid):
        with self.condition:return self.versions.get(uid,0)
    def wait(self,uid,version,timeout=10):
        with self.condition:
            self.condition.wait_for(lambda:self.versions.get(uid,0)!=version,timeout)
            return self.versions.get(uid,0)
