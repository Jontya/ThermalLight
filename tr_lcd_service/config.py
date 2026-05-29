import configparser
import os

_DEFAULTS = {
    'image_path': '',
    'resend_interval': '60',
    'log_level': 'INFO',
}

_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    def __init__(self):
        self.image_path: str = ''
        self.resend_interval: int = 60
        self.log_level: str = 'INFO'


def _config_path() -> str:
    return os.path.join(_SERVICE_DIR, 'config.ini')


def load_config() -> Config:
    config_path = _config_path()

    parser = configparser.ConfigParser(defaults=_DEFAULTS)
    parser.read(config_path, encoding='utf-8')

    cfg = Config()
    section = 'lcd'
    if parser.has_section(section):
        cfg.image_path = parser.get(section, 'image_path').strip()
        cfg.resend_interval = max(5, parser.getint(section, 'resend_interval', fallback=60))
        cfg.log_level = parser.get(section, 'log_level', fallback='INFO').upper()

    return cfg


def save_image_path(new_path: str) -> None:
    """Write image_path back to config.ini, preserving other keys."""
    config_path = _config_path()
    parser = configparser.ConfigParser(defaults=_DEFAULTS)
    parser.read(config_path, encoding='utf-8')
    if not parser.has_section('lcd'):
        parser.add_section('lcd')
    parser.set('lcd', 'image_path', new_path)
    with open(config_path, 'w', encoding='utf-8') as fh:
        parser.write(fh)
