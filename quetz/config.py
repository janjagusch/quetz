# Copyright 2020 QuantStack
# Distributed under the terms of the Modified BSD License.

import json
import logging
import logging.config
import os
from distutils.util import strtobool
from pathlib import Path
from secrets import token_bytes
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Type, Union

import appdirs
import pluggy
import toml
from pydantic import BaseSettings

from quetz import hooks, pkgstores
from quetz.errors import ConfigError

_filename = "config.toml"
_env_prefix = "QUETZ_"
_env_config_file = "CONFIG_FILE"
_site_dir = appdirs.site_config_dir("quetz")
_user_dir = appdirs.user_config_dir("quetz")

PAGINATION_LIMIT = 20


# class ConfigEntry(NamedTuple):
#     name: str
#     cast: Type
#     default: Any = None
#     required: bool = True

#     def full_name(self, section=""):
#         if section:
#             section += "_"
#         return f"{section}{self.name}"

#     def env_var(self, section=""):
#         return f"{_env_prefix}{self.full_name(section).upper()}"

#     def casted(self, value):
#         if self.cast is bool:
#             try:
#                 value = strtobool(str(value))
#             except ValueError as e:
#                 raise ConfigError(f"{self.name}: {e}")

#         return self.cast(value)


# class ConfigSection(NamedTuple):
#     name: str
#     entries: List[ConfigEntry]
#     required: bool = True


class SettingsGeneral(BaseSettings):
    package_unpack_threads: int = 1
    frontend_dir: str = ""
    redirect_http_to_https: bool = False

    class Config:
        env_prefix = 'general'


class SettingsCORS(BaseSettings):
    allow_origins: list = []
    allow_credentials: bool = True
    allow_methods: list[str] = ["*"]
    allow_headers: list[str] = ["*"]

    class Config:
        env_prefix = 'cors'


class SettingsGitHub(BaseSettings):
    client_id: str
    client_secret: str

    class Config:
        env_prefix = 'github'


class SettingsGitLab(BaseSettings):
    url: str = "https://gitlab.com"
    client_id: str
    client_secret: str

    class Config:
        env_prefix = 'gitlab'


class SettingsAzureAD(BaseSettings):
    client_id: str
    client_secret: str
    tenant_id: str

    class Config:
        env_prefix = 'azuread'


class SettingsSQLAlchemy(BaseSettings):
    database_url: str
    database_plugin_path: str = ""
    echo_sql: bool = False
    postgres_pool_size: int = 100
    postgres_max_overflow: int = 100

    class Config:
        env_prefix = 'sqlalchemy'


class SettingsSession(BaseSettings):
    secret: str
    https_only: bool = True

    class Config:
        env_prefix = 'session'


class SettingsLocalStore(BaseSettings):
    redirect_enabled: bool = False
    redirect_endpoint: str = "/files"
    redirect_secret: str = ""
    redirect_expiration: int = 3600

    class Config:
        env_prefix = 'local_store'


class SettingsS3(BaseSettings):
    access_key: str = ""
    secret_key: str = ""
    url: str = ""
    region: str = ""
    bucket_prefix: str = ""
    bucket_suffix: str = ""

    class Config:
        env_prefix = 's3'


class SettingsAzureBlob(BaseSettings):
    account_name: str = ""
    account_access_key: str = ""
    conn_str: str = ""
    container_prefix: str = ""
    container_suffix: str = ""

    class Config:
        env_prefix = 'azure_blob'


class SettingsGCS(BaseSettings):
    project: str = ""
    token: str = ""
    bucket_prefix: str = ""
    bucket_suffix: str = ""
    cache_timeout: int | None = None
    region: str | None = None

    class Config:
        env_prefix = 'gcs'


class SettingsGoogle(BaseSettings):
    client_id: str
    client_secret: str

    class Config:
        env_prefix = 'google'


class SettingsLogging(BaseSettings):
    level: str = "INFO"
    file: str = ""

    class Config:
        env_prefix = 'logging'


class SettingsUsers(BaseSettings):
    admins: list[str] = []
    maintainers: list[str] = []
    members: list[str] = []
    default_role: str | bool = False
    collect_emails: bool = False
    create_default_channel: bool = False

    class Config:
        env_prefix = 'users'


class SettingsWorker:
    type: str = "thread"
    redis_ip: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0

    class Config:
        env_prefix = 'worker'


class SettingsPlugins(BaseSettings):
    enabled: list[str] = []

    class Config:
        env_prefix = 'plugins'


class SettingsMirroring(BaseSettings):
    batch_length: int = 10
    batch_size: int = 1e8
    num_parallel_downloads: int = 10

    class Config:
        env_prefix = 'mirroring'


class SettingsQuotas(BaseSettings):
    channel_quota: int

    class Config:
        env_prefix = 'quotas'


class SettingsProfiling(BaseSettings):
    enable_sampling: bool = False
    interval_seconds: float = 0.001

    class Config:
        env_prefix = 'profiling'


class Settings(BaseSettings):
    general: SettingsGeneral = SettingsGeneral()
    cors: Optional[SettingsCORS] = None
    github: Optional[SettingsGitHub] = None
    gitlab: Optional[SettingsGitLab] = None
    azuread: Optional[SettingsAzureAD] = None
    sqlalchemy: SettingsSQLAlchemy
    session: SettingsSession
    local_store: SettingsLocalStore = SettingsLocalStore()
    s3: Optional[SettingsS3] = None
    azure_blob: Optional[SettingsAzureBlob] = None
    gcs: Optional[SettingsGCS] = None
    google: Optional[SettingsGoogle] = None
    logging: Optional[SettingsLogging] = None
    users: Optional[SettingsUsers] = None
    worker: Optional[SettingsWorker] = None
    plugins: SettingsPlugins = SettingsPlugins()
    mirroring: SettingsMirroring = SettingsMirroring()
    quotas: Optional[SettingsQuotas] = None
    profiling: Optional[SettingsProfiling] = None

    class Config:
        env_prefix = 'quetz'


class Config:
    _config_dirs = [_site_dir, _user_dir]
    _config_files = [os.path.join(d, _filename) for d in _config_dirs]

    _instances: Dict[Optional[str], "Config"] = {}

    def __new__(cls, deployment_config: str = None):
        if not deployment_config and None in cls._instances:
            return cls._instances[None]

        try:
            path = os.path.abspath(cls.find_file(deployment_config))
        except TypeError:
            # if not config path exists, set it to empty string.
            path = ""

        if path not in cls._instances:
            config = super().__new__(cls)
            config.init(path)
            cls._instances[path] = config
            # optimization - for default config path we also store the instance
            # under None key
            if not deployment_config:
                cls._instances[None] = config
        return cls._instances[path]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.config, name)

    @classmethod
    def find_file(cls, deployment_config: str = None):
        config_file_env = os.getenv(f"{_env_prefix}{_env_config_file}")
        deployment_config_files = []
        for f in (deployment_config, config_file_env):
            if f and os.path.isfile(f):
                deployment_config_files.append(f)

        # In order, get configuration from:
        # _site_dir, _user_dir, deployment_config, config_file_env
        for f in cls._config_files + deployment_config_files:
            if os.path.isfile(f):
                return f

    def init(self, path: str) -> None:
        """Load configurations from various places.

        Order of importance for configuration is:
        host < user profile < deployment < configuration file from env var < value from
        env var

        Parameters
        ----------
        deployment_config : str, optional
            The configuration stored at deployment level
        """

        self.config = self._read_config(path) if path else Settings()

        # self.config = Settings()

        # # only try to get config from config file if it exists.
        # if path:
        #     self.config.update(self._read_config(path))

        # self.config.update(self._get_environ_config())
        # self._trigger_update_config()

    # def _trigger_update_config(self):
    #     def set_entry_attr(entry, section=""):
    #         value = self._get_value(entry, section)

    #         setattr(self, entry.full_name(section), value)

    #     for item in self._config_map:
    #         if isinstance(item, ConfigSection) and (
    #             item.required or item.name in self.config
    #         ):
    #             for entry in item.entries:
    #                 set_entry_attr(entry, item.name)
    #         elif isinstance(item, ConfigEntry):
    #             set_entry_attr(item)

    # def _get_value(
    #     self, entry: ConfigEntry, section: str = ""
    # ) -> Union[str, bool, None]:
    #     """Get an entry value from a configuration mapping.

    #     Parameters
    #     ----------
    #     entry : ConfigEntry
    #         The entry to search
    #     section : str
    #         The section the entry belongs to

    #     Returns
    #     -------
    #     value : Union[str, bool]
    #         The entry value
    #     """
    #     try:
    #         if section:
    #             value = self.config[section][entry.name]
    #         else:
    #             value = self.config[entry.name]

    #         return entry.casted(value)

    #     except KeyError:
    #         if entry.default is not None:
    #             if callable(entry.default):
    #                 return entry.default()
    #             return entry.default

    #     msg = f"'{entry.name}' unset but no default specified"
    #     if section:
    #         msg += f" for section '{section}'"

    #     if entry.required:
    #         raise ConfigError(msg)

    #     return None

    def _read_config(self, filename: str) -> Settings:
        """Read a configuration file from its path.

        Parameters
        ----------
        filename : str
            The path of the configuration file

        Returns
        -------
        configuration : Settings
            The mapping of configuration variables found in the file
        """
        with open(filename) as f:
            try:
                return Settings(**toml.load(f))
            except toml.TomlDecodeError as e:
                raise ConfigError(f"failed to load config file '{filename}': {e}")

    # def _find_first_level_config(
    #     self, section_name: str
    # ) -> Union[ConfigSection, ConfigEntry, None]:
    #     """Find the section or entry at first level of config_map.

    #     Parameters
    #     ----------
    #     section_name : str
    #         The name of the section to find.

    #     Returns
    #     -------
    #     section : Union[ConfigSection, ConfigEntry, None]
    #         The section or entry found, else None.
    #     """
    #     for item in self._config_map:
    #         if section_name == item.name:
    #             return item
    #     return None

    # def _get_environ_config(self) -> Dict[str, Any]:
    #     """Looks into environment variables if some matches with config_map.

    #     Returns
    #     -------
    #     configuration : Dict[str, str]
    #         The mapping of configuration variables found in environment variables.
    #     """
    #     config: Dict[str, Any] = {}

    #     # get QUETZ environment variables.
    #     quetz_var = {
    #         key: value
    #         for key, value in os.environ.items()
    #         if key.startswith(_env_prefix)
    #     }
    #     for var, value in quetz_var.items():
    #         splitted_key = var.split('_')
    #         config_key = splitted_key[1].lower()
    #         idx = 2

    #         # look for the first level of config_map.
    #         # It must be done in loop as the key itself can contains '_'.
    #         first_level = None
    #         while idx < len(splitted_key):
    #             first_level = self._find_first_level_config(config_key)
    #             if first_level:
    #                 break
    #             config_key += f"_{ splitted_key[idx].lower()}"
    #             idx += 1

    #         # no first_level found, the variable is useless.
    #         if not first_level:
    #             continue
    #         # the first level is an entry, add it to the config.
    #         if isinstance(first_level, ConfigEntry):
    #             config[first_level.name] = value
    #         # the first level is a section.
    #         elif isinstance(first_level, ConfigSection):
    #             entry = "_".join(splitted_key[idx:]).lower()
    #             # the entry does not exist in section, the variable is useless.
    #             if entry not in [
    #                 section_entry.name for section_entry in first_level.entries
    #             ]:
    #                 continue
    #             # add the entry to the config.
    #             if first_level.name not in config:
    #                 config[first_level.name]: Dict[str, Any] = {}
    #             config[first_level.name]["_".join(splitted_key[idx:]).lower()] = value

    #     return config

    def get_package_store(self) -> pkgstores.PackageStore:
        """Return the appropriate package store as set in the config.

        Returns
        -------
        package_store : pkgstores.PackageStore
            The package store instance to enact package operations against
        """
        if self.config.get('s3'):
            return pkgstores.S3Store(
                {
                    'key': self.s3_access_key,
                    'secret': self.s3_secret_key,
                    'url': self.s3_url,
                    'region': self.s3_region,
                    'bucket_prefix': self.s3_bucket_prefix,
                    'bucket_suffix': self.s3_bucket_suffix,
                }
            )
        elif self.config.get('azure_blob'):
            return pkgstores.AzureBlobStore(
                {
                    'account_name': self.azure_blob_account_name,
                    'account_access_key': self.azure_blob_account_access_key,
                    'conn_str': self.azure_blob_conn_str,
                    'container_prefix': self.azure_blob_container_prefix,
                    'container_suffix': self.azure_blob_container_suffix,
                }
            )
        elif self.config.get('gcs'):
            return pkgstores.GoogleCloudStorageStore(
                {
                    'project': self.gcs_project,
                    'token': self.gcs_token,
                    'bucket_prefix': self.gcs_bucket_prefix,
                    'bucket_suffix': self.gcs_bucket_suffix,
                    'cache_timeout': self.gcs_cache_timeout,
                    'region': self.gcs_region,
                }
            )
        else:
            return pkgstores.LocalStore(
                {
                    'channels_dir': 'channels',
                    'redirect_enabled': self.local_store_redirect_enabled,
                    'redirect_endpoint': self.local_store_redirect_endpoint,
                    'redirect_secret': self.local_store_redirect_secret,
                    'redirect_expiration': int(self.local_store_redirect_expiration),
                }
            )

    def configured_section(self, section: str) -> bool:
        """Return if a given section has been configured.

        Parameters
        ----------
        provider: str
            The section name in config

        Returns
        -------
        bool
            Wether or not the given section is configured
        """
        return bool(getattr(self.config, section))

    # def register(self, extra_config: Iterable[ConfigSection]):
    #     """Register additional config variables"""
    #     self._config_map += extra_config
    #     self._trigger_update_config()


def create_config(
    client_id: str = "",
    client_secret: str = "",
    database_url: str = "sqlite:///./quetz.sqlite",
    secret: str = token_bytes(32).hex(),
    https: str = 'true',
) -> str:
    """Create a configuration file from a template.

    Parameters
    ----------
    client_id : str, optional
        The client ID {default=""}
    client_secret : str, optional
        The client secret {default=""}
    database_url : str, optional
        The URL of the database {default="sqlite:///./quetz.sqlite"}
    secret : str, optional
        The secret of the session {default=randomly create}
    https : str, optional
        Whether to use HTTPS, or not {default="true"}

    Returns
    -------
    configuration : str
        The configuration
    """
    with open(os.path.join(os.path.dirname(__file__), _filename), 'r') as f:
        config = ''.join(f.readlines())

    return config.format(client_id, client_secret, database_url, secret, https)


def colourized_formatter(fmt="", use_colors=True):
    try:
        from uvicorn.logging import ColourizedFormatter

        return ColourizedFormatter(fmt, use_colors=use_colors)
    except ImportError:
        return logging.Formatter(fmt)


def get_logger_config(config, loggers):
    if hasattr(config, "logging_level"):
        log_level = config.logging_level
    else:
        log_level = "INFO"

    if hasattr(config, "logging_file"):
        filename = config.logging_file
    else:
        filename = None

    log_level = os.environ.get("QUETZ_LOG_LEVEL", log_level)

    log_level = log_level.upper()

    handlers = ["console"]
    if filename:
        handlers.append("file")

    LOG_FORMATTERS = {
        "colour": {
            "()": "quetz.config.colourized_formatter",
            "fmt": "%(levelprefix)s [%(name)s] %(message)s",
            "use_colors": True,
        },
        "basic": {"format": "%(levelprefix)s [%(name)s] %(message)s"},
        "timestamp": {"format": '%(asctime)s %(levelname)s %(name)s  %(message)s'},
    }

    curdir = os.getcwd()

    LOG_HANDLERS = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "colour",
            "level": log_level,
            "stream": "ext://sys.stderr",
        },
        "file": {
            "class": "logging.FileHandler",
            "formatter": "timestamp",
            "filename": filename or os.path.join(curdir, "quetz.log"),
            "level": log_level,
        },
    }

    LOGGERS = {k: {"level": log_level, "handlers": handlers} for k in loggers}

    LOG_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": LOG_FORMATTERS,
        "handlers": LOG_HANDLERS,
        "loggers": LOGGERS,
    }

    return LOG_CONFIG


def configure_logger(config=None, loggers=("quetz", "urllib3.util.retry", "alembic")):
    """Get quetz logger"""

    log_config = get_logger_config(config, loggers)

    logging.config.dictConfig(log_config)


def get_plugin_manager(config=None) -> pluggy.PluginManager:
    """Create an instance of plugin manager."""

    if not config:
        config = Config()

    pm = pluggy.PluginManager("quetz")
    pm.add_hookspecs(hooks)
    if config.configured_section("plugins"):
        for name in config.plugins_enabled:
            pm.load_setuptools_entrypoints("quetz", name)
    else:
        pm.load_setuptools_entrypoints("quetz")
    return pm
