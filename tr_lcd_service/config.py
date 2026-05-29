import configparser
import os

_DEFAULTS = {
    'image_path': '',
    'resend_interval': '60',
    'log_level': 'INFO',
}


class Config:
    def __init__(self):
        self.image_path: str = ''
        self.resend_interval: int = 60
        self.log_level: str = 'INFO'


def load_config() -> Config:
    service_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(service_dir, 'config.ini')

    parser = configparser.ConfigParser(defaults=_DEFAULTS)
    parser.read(config_path, encoding='utf-8')

    cfg = Config()
    section = 'lcd'
    if parser.has_section(section):
        cfg.image_path = parser.get(section, 'image_path').strip()
        cfg.resend_interval = max(5, parser.getint(section, 'resend_interval', fallback=60))
        cfg.log_level = parser.get(section, 'log_level', fallback='INFO').upper()

    return cfg
