import unittest
from app.buffer_status import clean_buffer_report

class BufferStatusTests(unittest.TestCase):
    def report(self):
        return {'state':'active','items':[{'id':1,'loaded_bytes':50,'total_bytes':100,'state':'downloading','retries':1}],'player_seconds':12,'sample_age_seconds':4,'secret':'never retain'}
    def test_bounded_allowlist_and_recomputed_totals(self):
        report=self.report();report['downloaded_bytes']=99999999
        result=clean_buffer_report(report)
        self.assertEqual(result['downloaded_bytes'],50);self.assertNotIn('secret',result);self.assertEqual(result['sample_age_seconds'],4)
    def test_rejects_invalid_payload_without_raising(self):
        for value in [None,[],{},dict(self.report(),items=[{}]*4),dict(self.report(),player_seconds=float('nan')),dict(self.report(),sample_age_seconds=99),dict(self.report(),player_seconds=True)]:self.assertIsNone(clean_buffer_report(value))
    def test_oversized_song_does_not_hide_eligible_queue_telemetry(self):
        report=self.report();report['items'].insert(0,{'id':2,'loaded_bytes':0,'total_bytes':0,'state':'oversized'})
        result=clean_buffer_report(report)
        self.assertEqual(result['items'][0]['state'],'oversized')
        self.assertEqual(result['downloaded_bytes'],50)

    def test_large_disk_cache_counts_and_budget_are_preserved(self):
        report=self.report();report.update(storage='disk',limit_bytes=512*1024*1024)
        report['items']=[{'id':1,'loaded_bytes':91*1024*1024,'total_bytes':91*1024*1024,'state':'ready'}]
        result=clean_buffer_report(report)
        self.assertEqual(result['downloaded_bytes'],91*1024*1024)
        self.assertEqual(result['limit_bytes'],512*1024*1024)
        self.assertEqual(result['storage'],'disk')
        report['items']*=3
        report['limit_bytes']=96*1024*1024
        self.assertIsNone(clean_buffer_report(report))
    def test_oversized_untrusted_reports_are_rejected(self):
        report=self.report();report['items'][0]['total_bytes']=257*1024*1024
        self.assertIsNone(clean_buffer_report(report))
        report=self.report();report['limit_bytes']=1024*1024*1024
        self.assertIsNone(clean_buffer_report(report))
    def test_only_known_failure_reasons_are_retained(self):
        report=self.report();item=report['items'][0]
        item.update(state='retrying',failure_reason='unsupported-response')
        self.assertEqual(clean_buffer_report(report)['items'][0]['failure_reason'],'unsupported-response')
        item['failure_reason']='https://private.example/?secret=hidden'
        self.assertNotIn('failure_reason',clean_buffer_report(report)['items'][0])
    def test_worker_version_is_bounded_and_contains_no_arbitrary_details(self):
        report=self.report();report['worker_version']='20261108.2'
        self.assertEqual(clean_buffer_report(report)['worker_version'],'20261108.2')
        for value in ['x'*33,'https://secret.example/token',{},None]:
            report['worker_version']=value
            self.assertNotIn('worker_version',clean_buffer_report(report))
