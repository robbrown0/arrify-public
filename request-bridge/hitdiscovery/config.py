import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
import ipaddress

@dataclass
class Config:
    url: str = field(default_factory=lambda: os.getenv('LIDARR_URL', 'http://192.168.1.70:8687'))
    key_file: str = field(default_factory=lambda: os.getenv('LIDARR_API_KEY_FILE', '/run/secrets/lidarr_api_key'))
    data: Path = field(default_factory=lambda: Path(os.getenv('DATA_DIR', '/data')))
    reports: Path = field(default_factory=lambda: Path(os.getenv('REPORT_DIR', '/reports')))
    acquire: bool = field(default_factory=lambda: os.getenv('ACQUIRE_ENABLED', 'false').lower() == 'true')
    reviewed: bool = field(default_factory=lambda: os.getenv('INITIAL_REPORT_REVIEWED', 'false').lower() == 'true')
    weekly_limit: int = field(default_factory=lambda: int(os.getenv('WEEKLY_ALBUM_LIMIT', '10')))
    resolve_limit: int = field(default_factory=lambda: int(os.getenv('RESOLVE_LIMIT', '150')))
    music_paths: tuple = field(default_factory=lambda: tuple(filter(None, os.getenv('MUSIC_PATHS', '/music').split(':'))))
    quality_id: int = field(default_factory=lambda: int(os.getenv('QUALITY_PROFILE_ID', '0')))
    metadata_id: int = field(default_factory=lambda: int(os.getenv('METADATA_PROFILE_ID', '0')))
    root: str = field(default_factory=lambda: os.getenv('ROOT_FOLDER', ''))
    contact: str = field(default_factory=lambda: os.getenv('MUSICBRAINZ_CONTACT', ''))
    historical: str = field(default_factory=lambda: os.getenv('HISTORICAL_CHART_FILE', ''))
    catalog_db: str = field(default_factory=lambda: os.getenv('EXISTING_CATALOG_DB', ''))
    wikimedia: bool = field(default_factory=lambda: os.getenv('WIKIMEDIA_HISTORICAL', 'true').lower() == 'true')
    expansion: bool = field(default_factory=lambda: os.getenv('EXPANSION_ENABLED','false').lower()=='true')
    budget_bytes: int = field(default_factory=lambda: int(os.getenv('DOWNLOAD_BUDGET_BYTES',os.getenv('ACQUISITION_BUDGET_BYTES','0'))))
    budget_date: str = field(default_factory=lambda: os.getenv('DOWNLOAD_BUDGET_DATE',''))
    budget_timezone: str = field(default_factory=lambda: os.getenv('DOWNLOAD_BUDGET_TIMEZONE','America/New_York'))
    chart_year_target: int = field(default_factory=lambda: int(os.getenv('HITS_PER_CHART_YEAR','100')))
    core_rank: int = field(default_factory=lambda: int(os.getenv('CORE_YEAR_END_RANK','50')))
    batch_size: int = field(default_factory=lambda: int(os.getenv('EXPANSION_BATCH_SIZE','10')))
    concurrent_downloads: int = field(default_factory=lambda: int(os.getenv('MAX_CONCURRENT_DOWNLOADS','5')))
    discovery_seconds: int = field(default_factory=lambda: int(os.getenv('DISCOVERY_INTERVAL_SECONDS','3600')))
    maintenance_seconds: int = field(default_factory=lambda: int(os.getenv('MAINTENANCE_INTERVAL_SECONDS','900')))
    prepared_target: int = field(default_factory=lambda: int(os.getenv('PREPARED_QUEUE_TARGET','60')))
    preparation_seconds: int = field(default_factory=lambda: int(os.getenv('PREPARATION_TIME_LIMIT_SECONDS','90')))
    ownership_scope: str = field(default_factory=lambda: os.getenv('OWNERSHIP_SCOPE', 'Lidarr and configured read-only music paths'))
    def validate(self):
        host = urlparse(self.url).hostname or ''
        try:
            addr = ipaddress.ip_address(host)
            if not (addr.is_private or addr.is_loopback):
                raise ValueError('Lidarr must use a private address')
        except ValueError:
            if host not in ('lidarr', 'localhost', 'host.docker.internal'):
                raise ValueError('Use a private numeric Lidarr IP or the lidarr Docker hostname')
        if self.weekly_limit < 0 or self.resolve_limit < 0:
            raise ValueError('Limits must be nonnegative')
        if self.expansion and (self.budget_bytes<=0 or self.batch_size<1 or self.concurrent_downloads<1 or min(self.discovery_seconds,self.maintenance_seconds)<60):
            raise ValueError('Expansion requires a positive budget and valid limits')
        if self.expansion:
            from datetime import date
            from zoneinfo import ZoneInfo
            if not self.budget_date: raise ValueError('Set DOWNLOAD_BUDGET_DATE; the download allowance does not auto-renew')
            if self.budget_date!='daily': date.fromisoformat(self.budget_date)
            ZoneInfo(self.budget_timezone)
        if min(self.prepared_target,self.preparation_seconds)<1: raise ValueError('Preparation limits must be positive')
        if not 1<=self.core_rank<=self.chart_year_target<=100: raise ValueError('Chart rank targets must be between 1 and 100')
        self.data.mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)
