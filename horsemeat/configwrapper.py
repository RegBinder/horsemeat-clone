# vim: set expandtab ts=4 sw=4 filetype=python:

import abc
import contextlib
import datetime
import json
import logging
import logging.config
import math
import smtplib
import sys
import textwrap
import traceback
import uuid
import warnings

import clepy
import jinja2
import pkg_resources
import psycopg2, psycopg2.extras
import pyrax
import yaml

from horsemeat import fancyjsondumps

log = logging.getLogger(__name__)

class ConfigWrapper(object):

    """
    Use this class to interact with our configuration files.  Add properties
    and methods so that app code doesn't have to think too much about the
    way the data is stored in the config files, and so that we can
    reorganize stuff.
    """

    __metaclass__ = abc.ABCMeta

    # Subclasses must define this configmodule attribute.  There's
    # nothing in the abc module that I can use to force that to happen,
    # but it sure would be nice.  In the subclass, set it to a string
    # like:
    #
    #   "trailhead.configs.yaml_files"
    #
    configmodule = None

    # Later, you can set up a particular instance as the default
    # instance, by using the set_as_default instance method.
    default_instance = None

    # Keep a reference of the instances made from each yaml file.
    instances = dict()

    # Just an alias.
    already_instantiated = instances

    # Keep these things at the class level, so that all instances share
    # them.
    postgresql_connection = None
    jinja2_environment = None
    smtp_connection = None
    pyrax_connection = None

    def __init__(self, config_dictionary):
        self.config_dictionary = config_dictionary

    @classmethod
    def from_yaml_file_name(cls, filename, force_reload=False):

        """
        Loads one of the yaml files in the config_files folder.

        Calling this repeatedly for the same file WILL NOT reload the
        file unless you set force_reload to True.

        If you are modifying the ConfigWrapper instance, then you're
        doing something wrong.

        >>> ConfigWrapper.from_yaml_file_name('matt.yaml') # doctest: +SKIP
        <ConfigWrapper>

        """
        if not filename:
            raise ValueError("Sorry, I need a filename!")

        elif not cls.configmodule:
            raise ValueError("Sorry, you need to set cls.configmodule!")

        if filename in cls.instances and not force_reload:
            return cls.instances[filename]

        else:

            stream = pkg_resources.resource_stream(
                cls.configmodule,
                filename)

            self = cls(yaml.load(stream))

            cls.instances[filename] = self

            return self

    @property
    def should_register_composite_types(self):

          return self.config_dictionary['postgresql'].get(
               'should_register_composite_types', False)

    def get_postgresql_connection(self, register_composite_types=True):

        if not self.postgresql_connection:

            self.make_database_connection(
                register_composite_types=register_composite_types)

        return self.postgresql_connection

    # Short aliases are fun too.
    get_pgconn = get_postgresql_connection

    @contextlib.contextmanager
    def get_autocommitting_postgresql_connection(self):

        """
        Use this thing like this::

        >>> with cw.get_autocommitting_postgresql_connection \
        ... as pgconn: # doctest: +SKIP
        ...     cursor = pgconn.cursor()
        ...     cursor.execute("update ...")

        and the transaction will be committed when you leave the with
        block.

        """

        try:
            yield self.get_postgresql_connection()

        finally:
            log.info("Committing postgresql connection...")
            self.postgresql_connection.commit()

    @property
    def pyrax_username(self):
        return self.config_dictionary['cloudfiles']['username']

    @property
    def pyrax_api_key(self):
        return self.config_dictionary['cloudfiles']['api-key'],

    @property
    def pyrax_region(self):
        return "ORD"

    @property
    def database_host(self):
        return self.config_dictionary['postgresql']['host']

    @property
    def database_port(self):
        return self.config_dictionary['postgresql']['port']

    @property
    def database_database(self):
        return self.config_dictionary['postgresql']['database']

    # Since database_database sounds stupid, I'm setting up an alias
    # "database_name".
    database_name = database_database

    @property
    def database_user(self):
        return self.config_dictionary['postgresql']['user']

    @property
    def database_password(self):
        return self.config_dictionary['postgresql']['password']

    def make_database_connection(self, register_composite_types=True):

        pgconn = psycopg2.connect(
            connection_factory=psycopg2.extras.NamedTupleConnection,
            port=self.database_port,
            database=self.database_name,
            host=self.database_host,
            user=self.database_user,
            password=self.database_password)

        log.info("Just made postgresql connection {0}.".format(
            pgconn))

        # Keep a reference to this connection on the class, so that
        # other instances can just recycle this connection.
        self.__class__.postgresql_connection = pgconn

        psycopg2.extras.register_uuid()
        psycopg2.extras.register_hstore(pgconn, globally=True)

        psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
        psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)

        if register_composite_types:
            self.register_composite_types(pgconn)
            log.debug('Registered project composite types')

        return pgconn

    # Make aliases because Matt can't remember stuff good.
    create_postgresql_connection = make_database_connection
    make_postgresql_connection = make_database_connection

    @abc.abstractproperty
    def register_composite_types(self, pgconn):
        raise NotImplementedError

    def configure_logging(self, process_type='default'):

        if 'logging' not in self.config_dictionary:
            raise MissingConfig("You need to set a logging key!")

        elif process_type not in self.config_dictionary['logging']:

            raise MissingConfig("I don't know how to configure "
                "logging for {0} processes!  You need to set a key for "
                "{0} processes in the logging section.".format(
                    process_type))

        else:

            x = self.config_dictionary['logging'][process_type]

            if isinstance(x, str):

                warnings.warn("Stop it! Define logging configuration "
                    "the same YAML file!")

                logging.config.fileConfig(
                    pkg_resources.resource_filename(
                        'horsemeat.configs.logging_configs',
                        x))

            elif isinstance(x, dict):
                logging.config.dictConfig(x)

            else:
                raise Exception("can not deal with x: {0}.".format(x))

        log.info('Just configured logging...')

    def set_as_default(self):

        ConfigWrapper.default_instance = self
        return self

    @classmethod
    def get_default(cls):

        if cls.default_instance:
            return cls.default_instance

        else:
            raise ValueError("Sorry!  No default configwrapper "
                "instance has been set!")

    @property
    def app_secret(self):

        try:
            return self.config_dictionary['app']['secret']

        except KeyError:
            raise MissingConfig("Didn't find an app secret!")

    @property
    def cloud_base_url(self):

        try:
            return self.config_dictionary['cloudfiles']['base-url']

        except KeyError:
            raise MissingConfig("Didn't find a cloudfiles base url!")

    @property
    def cloud_secret_key(self):

        try:
            return self.config_dictionary['cloudfiles']['temp-url-key']

        except KeyError:
            raise MissingConfig("Didn't find a cloudfiles secret key!")

    @property
    def cloud_file_time_available(self):

        try:
            return int(self.config_dictionary['cloudfiles']['time-available'])

        except KeyError:
            raise MissingConfig("Didn't find cloudfiles time available!")

    def make_jinja2_environment(self):

        j = jinja2.Environment(
            autoescape=True,

            # Tell jinja2 to blow up when we use an undefined name
            # in a template.
            undefined=jinja2.StrictUndefined,

            loader=jinja2.PrefixLoader({
            }),

            extensions=['jinja2.ext.loopcontrols'],
        )

        log.info("Just built a jinja2 environment")

        self.__class__.jinja2_environment = j

        # Add a bunch of stuff to the template namespace.
        j.globals['ceil'] = math.ceil
        j.globals['clepy'] = clepy
        j.globals['datetime'] = datetime
        j.globals['dir'] = dir
        j.globals['enumerate'] = enumerate
        j.globals['float'] = float
        j.globals['getattr'] = getattr
        j.globals['hasattr'] = hasattr
        j.globals['id'] = id
        j.globals['int'] = int
        j.globals['json'] = json
        j.globals['fancyjsondumps'] = fancyjsondumps
        j.globals['len'] = len
        j.globals['mathset'] = set
        j.globals['max'] = max
        j.globals['round'] = round
        j.globals['sorted'] = sorted
        j.globals['str'] = str
        j.globals['type'] = type
        j.globals['uuid'] = uuid
        j.globals['zip'] = zip

        # Give jinja a reference to the configwrapper.
        j.globals['cw'] = self

        self.add_more_stuff_to_jinja2_globals()

        return j

    def add_more_stuff_to_jinja2_globals(self):
        log.info(textwrap.dedent("""
            Nothing extra to add.  You can add stuff in your
            subclass if you want to..
            """))

    def get_jinja2_environment(self):

        if not self.jinja2_environment:
            self.make_jinja2_environment()

        return self.jinja2_environment

    @property
    def j(self):
        return sef.get_jinja2_environment()


    @property
    def scheme(self):
        return self.config_dictionary['app']['scheme']

    @property
    def host(self):
        return self.config_dictionary['app']['host']

    @property
    def web_host(self):
        return '{scheme}://{host}'.format(
            scheme=self.scheme,
            host=self.host)

    def make_location_from_path(self, path):

        """
        >>> cw = ConfigWrapper({'app': {'scheme': 'http', 'host': 'example.com'}})
        >>> cw.make_location_from_path('/login')
        'http://example.com/login'

        """

        return '{0}://{1}{2}'.format(
            self.scheme,
            self.host,
            path)

    @property
    def smtp_host(self):
        return self.config_dictionary['smtp']['host']

    def make_smtp_connection(self):

        """
        I'm not trying any fancy recycled connection stuff here.  It
        seems like the localhost SMTP server closes old connections
        automatically.
        """

        return smtplib.SMTP(self.smtp_host)

    @property
    def launch_debugger_on_error(self):

        """
        This returns False if the config file has no entry.
        """

        if 'launch_debugger_on_error' \
        not in self.config_dictionary['app']:
            return False

        else:
            return self.config_dictionary['app']['launch_debugger_on_error']

    def do_pyrax_stuff(self):

        """
        Probably needs a better name, like "authenticate with pyrax".
        """

        log.info("Setting up a new pyrax object...")

        if self.pyrax_connection:
            warnings.warn("NO! Use self.get_pyrax_connection instead!")

        pyrax.set_setting('identity_type',  'rackspace')

        pyrax.set_credentials(

            self.config_dictionary['cloudfiles']['username'],
            self.config_dictionary['cloudfiles']['api-key'],
            region="ORD")

        log.info("Set settings and credentials on pyrax")

        return pyrax

    def get_pyrax_connection(self):

        """
        Just like the other get_* methods, this returns an
        already-created instance if it exists.
        """

        if not self.pyrax_connection:
            self.pyrax_connection = self.do_pyrax_stuff()

        return self.pyrax_connection


    def connect_everything(self):

        """
        Make connections to all the external services we need.
        """

        # TODO: figure out an elegant way for subclasses to run this as
        # well as their own on-bootup stuff.

        self.get_postgresql_connection()
        self.get_pyrax_connection()

    @property
    def dev_mode(self):

        return 'dev' == self.config_dictionary['app'].get(
            'mode',
            'production')

    @property
    def production_mode(self):
        return not self.dev_mode

    def build_webapp(self):

        self.set_as_default()
        self.configure_logging()

        if self.production_mode:
            self.run_production_mode_stuff()

        j = self.get_jinja2_environment()
        pgconn = self.get_postgresql_connection()

        return self.dispatcher_class(j, pgconn, self)

    def run_production_mode_stuff(self):

        """
        Subclasses can add stuff in here if they want.
        """

        log.info("Nothing to do for production-mode stuff")

    @abc.abstractproperty
    def dispatcher_class(self):

        raise NotImplementedError

class MissingConfig(KeyError):

    """
    You need to add some more stuff to your config file!
    """

def log_uncaught_exceptions(ex_cls, ex, tb):
    log.critical(''.join(traceback.format_tb(tb)))
    log.critical('{0}: {1}'.format(ex_cls, ex))

    if hasattr(ex, 'errors') and ex.errors:
        log.critical(ex.errors)
