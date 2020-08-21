# engine/default.py
# Copyright (C) 2005-2020 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: http://www.opensource.org/licenses/mit-license.php

"""Default implementations of per-dialect sqlalchemy.engine classes.

These are semi-private implementation classes which are only of importance
to database dialect authors; dialects will usually use the classes here
as the base class for their own corresponding classes.

"""

import codecs
import random
import re
import weakref

from . import cursor as _cursor
from . import interfaces
from .. import event
from .. import exc
from .. import Integer
from .. import pool
from .. import processors
from .. import types as sqltypes
from .. import util
from ..sql import compiler
from ..sql import expression
from ..sql.elements import quoted_name

AUTOCOMMIT_REGEXP = re.compile(
    r"\s*(?:UPDATE|INSERT|CREATE|DELETE|DROP|ALTER)", re.I | re.UNICODE
)

# When we're handed literal SQL, ensure it's a SELECT query
SERVER_SIDE_CURSOR_RE = re.compile(r"\s*SELECT", re.I | re.UNICODE)


CACHE_HIT = util.symbol("CACHE_HIT")
CACHE_MISS = util.symbol("CACHE_MISS")
CACHING_DISABLED = util.symbol("CACHING_DISABLED")
NO_CACHE_KEY = util.symbol("NO_CACHE_KEY")


class DefaultDialect(interfaces.Dialect):
    """Default implementation of Dialect"""

    statement_compiler = compiler.SQLCompiler
    ddl_compiler = compiler.DDLCompiler
    type_compiler = compiler.GenericTypeCompiler
    preparer = compiler.IdentifierPreparer
    supports_alter = True
    supports_comments = False
    inline_comments = False

    # the first value we'd get for an autoincrement
    # column.
    default_sequence_base = 1

    # most DBAPIs happy with this for execute().
    # not cx_oracle.
    execute_sequence_format = tuple

    sequence_default_column_type = Integer

    supports_views = True
    supports_sequences = False
    sequences_optional = False
    preexecute_autoincrement_sequences = False
    postfetch_lastrowid = True
    implicit_returning = False
    full_returning = False
    insert_executemany_returning = False

    cte_follows_insert = False

    supports_native_enum = False
    supports_native_boolean = False
    non_native_boolean_check_constraint = True

    supports_simple_order_by_label = True

    tuple_in_values = False

    engine_config_types = util.immutabledict(
        [
            ("convert_unicode", util.bool_or_str("force")),
            ("pool_timeout", util.asint),
            ("echo", util.bool_or_str("debug")),
            ("echo_pool", util.bool_or_str("debug")),
            ("pool_recycle", util.asint),
            ("pool_size", util.asint),
            ("max_overflow", util.asint),
        ]
    )

    # if the NUMERIC type
    # returns decimal.Decimal.
    # *not* the FLOAT type however.
    supports_native_decimal = False

    if util.py3k:
        supports_unicode_statements = True
        supports_unicode_binds = True
        returns_unicode_strings = sqltypes.String.RETURNS_UNICODE
        description_encoding = None
    else:
        supports_unicode_statements = False
        supports_unicode_binds = False
        returns_unicode_strings = sqltypes.String.RETURNS_UNKNOWN
        description_encoding = "use_encoding"

    name = "default"

    # length at which to truncate
    # any identifier.
    max_identifier_length = 9999
    _user_defined_max_identifier_length = None

    isolation_level = None

    # length at which to truncate
    # the name of an index.
    # Usually None to indicate
    # 'use max_identifier_length'.
    # thanks to MySQL, sigh
    max_index_name_length = None

    supports_sane_rowcount = True
    supports_sane_multi_rowcount = True
    colspecs = {}
    default_paramstyle = "named"
    supports_default_values = False
    supports_empty_insert = True
    supports_multivalues_insert = False

    supports_is_distinct_from = True

    supports_server_side_cursors = False

    # extra record-level locking features (#4860)
    supports_for_update_of = False

    server_version_info = None

    default_schema_name = None

    construct_arguments = None
    """Optional set of argument specifiers for various SQLAlchemy
    constructs, typically schema items.

    To implement, establish as a series of tuples, as in::

        construct_arguments = [
            (schema.Index, {
                "using": False,
                "where": None,
                "ops": None
            })
        ]

    If the above construct is established on the PostgreSQL dialect,
    the :class:`.Index` construct will now accept the keyword arguments
    ``postgresql_using``, ``postgresql_where``, nad ``postgresql_ops``.
    Any other argument specified to the constructor of :class:`.Index`
    which is prefixed with ``postgresql_`` will raise :class:`.ArgumentError`.

    A dialect which does not include a ``construct_arguments`` member will
    not participate in the argument validation system.  For such a dialect,
    any argument name is accepted by all participating constructs, within
    the namespace of arguments prefixed with that dialect name.  The rationale
    here is so that third-party dialects that haven't yet implemented this
    feature continue to function in the old way.

    .. versionadded:: 0.9.2

    .. seealso::

        :class:`.DialectKWArgs` - implementing base class which consumes
        :attr:`.DefaultDialect.construct_arguments`


    """

    # indicates symbol names are
    # UPPERCASEd if they are case insensitive
    # within the database.
    # if this is True, the methods normalize_name()
    # and denormalize_name() must be provided.
    requires_name_normalize = False

    reflection_options = ()

    dbapi_exception_translation_map = util.immutabledict()
    """mapping used in the extremely unusual case that a DBAPI's
    published exceptions don't actually have the __name__ that they
    are linked towards.

    .. versionadded:: 1.0.5

    """

    CACHE_HIT = CACHE_HIT
    CACHE_MISS = CACHE_MISS
    CACHING_DISABLED = CACHING_DISABLED
    NO_CACHE_KEY = NO_CACHE_KEY

    @util.deprecated_params(
        convert_unicode=(
            "1.3",
            "The :paramref:`_sa.create_engine.convert_unicode` parameter "
            "and corresponding dialect-level parameters are deprecated, "
            "and will be removed in a future release.  Modern DBAPIs support "
            "Python Unicode natively and this parameter is unnecessary.",
        ),
        empty_in_strategy=(
            "1.4",
            "The :paramref:`_sa.create_engine.empty_in_strategy` keyword is "
            "deprecated, and no longer has any effect.  All IN expressions "
            "are now rendered using "
            'the "expanding parameter" strategy which renders a set of bound'
            'expressions, or an "empty set" SELECT, at statement execution'
            "time.",
        ),
        case_sensitive=(
            "1.4",
            "The :paramref:`_sa.create_engine.case_sensitive` parameter "
            "is deprecated and will be removed in a future release. "
            "Applications should work with result column names in a case "
            "sensitive fashion.",
        ),
    )
    def __init__(
        self,
        convert_unicode=False,
        encoding="utf-8",
        paramstyle=None,
        dbapi=None,
        implicit_returning=None,
        case_sensitive=True,
        supports_native_boolean=None,
        max_identifier_length=None,
        label_length=None,
        # int() is because the @deprecated_params decorator cannot accommodate
        # the direct reference to the "NO_LINTING" object
        compiler_linting=int(compiler.NO_LINTING),
        **kwargs
    ):

        if not getattr(self, "ported_sqla_06", True):
            util.warn(
                "The %s dialect is not yet ported to the 0.6 format"
                % self.name
            )

        self.convert_unicode = convert_unicode
        self.encoding = encoding
        self.positional = False
        self._ischema = None
        self.dbapi = dbapi
        if paramstyle is not None:
            self.paramstyle = paramstyle
        elif self.dbapi is not None:
            self.paramstyle = self.dbapi.paramstyle
        else:
            self.paramstyle = self.default_paramstyle
        if implicit_returning is not None:
            self.implicit_returning = implicit_returning
        self.positional = self.paramstyle in ("qmark", "format", "numeric")
        self.identifier_preparer = self.preparer(self)
        self.type_compiler = self.type_compiler(self)
        if supports_native_boolean is not None:
            self.supports_native_boolean = supports_native_boolean
        self.case_sensitive = case_sensitive

        self._user_defined_max_identifier_length = max_identifier_length
        if self._user_defined_max_identifier_length:
            self.max_identifier_length = (
                self._user_defined_max_identifier_length
            )
        self.label_length = label_length
        self.compiler_linting = compiler_linting
        if self.description_encoding == "use_encoding":
            self._description_decoder = (
                processors.to_unicode_processor_factory
            )(encoding)
        elif self.description_encoding is not None:
            self._description_decoder = (
                processors.to_unicode_processor_factory
            )(self.description_encoding)
        self._encoder = codecs.getencoder(self.encoding)
        self._decoder = processors.to_unicode_processor_factory(self.encoding)

    @util.memoized_property
    def _type_memos(self):
        return weakref.WeakKeyDictionary()

    @property
    def dialect_description(self):
        return self.name + "+" + self.driver

    @property
    def supports_sane_rowcount_returning(self):
        """True if this dialect supports sane rowcount even if RETURNING is
        in use.

        For dialects that don't support RETURNING, this is synonymous with
        ``supports_sane_rowcount``.

        """
        return self.supports_sane_rowcount

    @classmethod
    def get_pool_class(cls, url):
        return getattr(cls, "poolclass", pool.QueuePool)

    def get_dialect_pool_class(self, url):
        return self.get_pool_class(url)

    @classmethod
    def load_provisioning(cls):
        package = ".".join(cls.__module__.split(".")[0:-1])
        try:
            __import__(package + ".provision")
        except ImportError:
            pass

    def initialize(self, connection):
        try:
            self.server_version_info = self._get_server_version_info(
                connection
            )
        except NotImplementedError:
            self.server_version_info = None
        try:
            self.default_schema_name = self._get_default_schema_name(
                connection
            )
        except NotImplementedError:
            self.default_schema_name = None

        try:
            self.default_isolation_level = self.get_isolation_level(
                connection.connection
            )
        except NotImplementedError:
            self.default_isolation_level = None

        if self.returns_unicode_strings is sqltypes.String.RETURNS_UNKNOWN:
            if util.py3k:
                raise exc.InvalidRequestError(
                    "RETURNS_UNKNOWN is unsupported in Python 3"
                )
            self.returns_unicode_strings = self._check_unicode_returns(
                connection
            )

        if (
            self.description_encoding is not None
            and self._check_unicode_description(connection)
        ):
            self._description_decoder = self.description_encoding = None

        if not self._user_defined_max_identifier_length:
            max_ident_length = self._check_max_identifier_length(connection)
            if max_ident_length:
                self.max_identifier_length = max_ident_length

        if (
            self.label_length
            and self.label_length > self.max_identifier_length
        ):
            raise exc.ArgumentError(
                "Label length of %d is greater than this dialect's"
                " maximum identifier length of %d"
                % (self.label_length, self.max_identifier_length)
            )

    def on_connect(self):
        # inherits the docstring from interfaces.Dialect.on_connect
        return None

    def _check_max_identifier_length(self, connection):
        """Perform a connection / server version specific check to determine
        the max_identifier_length.

        If the dialect's class level max_identifier_length should be used,
        can return None.

        .. versionadded:: 1.3.9

        """
        return None

    def _check_unicode_returns(self, connection, additional_tests=None):
        # this now runs in py2k only and will be removed in 2.0; disabled for
        # Python 3 in all cases under #5315
        if util.py2k and not self.supports_unicode_statements:
            cast_to = util.binary_type
        else:
            cast_to = util.text_type

        if self.positional:
            parameters = self.execute_sequence_format()
        else:
            parameters = {}

        def check_unicode(test):
            statement = cast_to(expression.select(test).compile(dialect=self))
            try:
                cursor = connection.connection.cursor()
                connection._cursor_execute(cursor, statement, parameters)
                row = cursor.fetchone()
                cursor.close()
            except exc.DBAPIError as de:
                # note that _cursor_execute() will have closed the cursor
                # if an exception is thrown.
                util.warn(
                    "Exception attempting to "
                    "detect unicode returns: %r" % de
                )
                return False
            else:
                return isinstance(row[0], util.text_type)

        tests = [
            # detect plain VARCHAR
            expression.cast(
                expression.literal_column("'test plain returns'"),
                sqltypes.VARCHAR(60),
            ),
            # detect if there's an NVARCHAR type with different behavior
            # available
            expression.cast(
                expression.literal_column("'test unicode returns'"),
                sqltypes.Unicode(60),
            ),
        ]

        if additional_tests:
            tests += additional_tests

        results = {check_unicode(test) for test in tests}

        if results.issuperset([True, False]):
            return sqltypes.String.RETURNS_CONDITIONAL
        else:
            return (
                sqltypes.String.RETURNS_UNICODE
                if results == {True}
                else sqltypes.String.RETURNS_BYTES
            )

    def _check_unicode_description(self, connection):
        # all DBAPIs on Py2K return cursor.description as encoded

        if util.py2k and not self.supports_unicode_statements:
            cast_to = util.binary_type
        else:
            cast_to = util.text_type

        cursor = connection.connection.cursor()
        try:
            cursor.execute(
                cast_to(
                    expression.select(
                        expression.literal_column("'x'").label("some_label")
                    ).compile(dialect=self)
                )
            )
            return isinstance(cursor.description[0][0], util.text_type)
        finally:
            cursor.close()

    def type_descriptor(self, typeobj):
        """Provide a database-specific :class:`.TypeEngine` object, given
        the generic object which comes from the types module.

        This method looks for a dictionary called
        ``colspecs`` as a class or instance-level variable,
        and passes on to :func:`_types.adapt_type`.

        """
        return sqltypes.adapt_type(typeobj, self.colspecs)

    def has_index(self, connection, table_name, index_name, schema=None):
        if not self.has_table(connection, table_name, schema=schema):
            return False
        for idx in self.get_indexes(connection, table_name, schema=schema):
            if idx["name"] == index_name:
                return True
        else:
            return False

    def validate_identifier(self, ident):
        if len(ident) > self.max_identifier_length:
            raise exc.IdentifierError(
                "Identifier '%s' exceeds maximum length of %d characters"
                % (ident, self.max_identifier_length)
            )

    def connect(self, *cargs, **cparams):
        # inherits the docstring from interfaces.Dialect.connect
        return self.dbapi.connect(*cargs, **cparams)

    def create_connect_args(self, url):
        # inherits the docstring from interfaces.Dialect.create_connect_args
        opts = url.translate_connect_args()
        opts.update(url.query)
        return [[], opts]

    def set_engine_execution_options(self, engine, opts):
        if "isolation_level" in opts:
            isolation_level = opts["isolation_level"]

            @event.listens_for(engine, "engine_connect")
            def set_isolation(connection, branch):
                if not branch:
                    self._set_connection_isolation(connection, isolation_level)

    def set_connection_execution_options(self, connection, opts):
        if "isolation_level" in opts:
            self._set_connection_isolation(connection, opts["isolation_level"])

    def _set_connection_isolation(self, connection, level):
        if connection.in_transaction():
            if connection._is_future:
                raise exc.InvalidRequestError(
                    "This connection has already begun a transaction; "
                    "isolation level may not be altered until transaction end"
                )
            else:
                util.warn(
                    "Connection is already established with a Transaction; "
                    "setting isolation_level may implicitly rollback or "
                    "commit "
                    "the existing transaction, or have no effect until "
                    "next transaction"
                )
        self.set_isolation_level(connection.connection, level)
        connection.connection._connection_record.finalize_callback.append(
            self.reset_isolation_level
        )

    def do_begin(self, dbapi_connection):
        pass

    def do_rollback(self, dbapi_connection):
        dbapi_connection.rollback()

    def do_commit(self, dbapi_connection):
        dbapi_connection.commit()

    def do_close(self, dbapi_connection):
        dbapi_connection.close()

    @util.memoized_property
    def _dialect_specific_select_one(self):
        return str(expression.select([1]).compile(dialect=self))

    def do_ping(self, dbapi_connection):
        cursor = None
        try:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(self._dialect_specific_select_one)
            finally:
                cursor.close()
        except self.dbapi.Error as err:
            if self.is_disconnect(err, dbapi_connection, cursor):
                return False
            else:
                raise
        else:
            return True

    def create_xid(self):
        """Create a random two-phase transaction ID.

        This id will be passed to do_begin_twophase(), do_rollback_twophase(),
        do_commit_twophase().  Its format is unspecified.
        """

        return "_sa_%032x" % random.randint(0, 2 ** 128)

    def do_savepoint(self, connection, name):
        connection.execute(expression.SavepointClause(name))

    def do_rollback_to_savepoint(self, connection, name):
        connection.execute(expression.RollbackToSavepointClause(name))

    def do_release_savepoint(self, connection, name):
        connection.execute(expression.ReleaseSavepointClause(name))

    def do_executemany(self, cursor, statement, parameters, context=None):
        cursor.executemany(statement, parameters)

    def do_execute(self, cursor, statement, parameters, context=None):
        cursor.execute(statement, parameters)

    def do_execute_no_params(self, cursor, statement, context=None):
        cursor.execute(statement)

    def is_disconnect(self, e, connection, cursor):
        return False

    def reset_isolation_level(self, dbapi_conn):
        # default_isolation_level is read from the first connection
        # after the initial set of 'isolation_level', if any, so is
        # the configured default of this dialect.
        self.set_isolation_level(dbapi_conn, self.default_isolation_level)

    def normalize_name(self, name):
        if name is None:
            return None
        if util.py2k:
            if isinstance(name, str):
                name = name.decode(self.encoding)

        name_lower = name.lower()
        name_upper = name.upper()

        if name_upper == name_lower:
            # name has no upper/lower conversion, e.g. non-european characters.
            # return unchanged
            return name
        elif name_upper == name and not (
            self.identifier_preparer._requires_quotes
        )(name_lower):
            # name is all uppercase and doesn't require quoting; normalize
            # to all lower case
            return name_lower
        elif name_lower == name:
            # name is all lower case, which if denormalized means we need to
            # force quoting on it
            return quoted_name(name, quote=True)
        else:
            # name is mixed case, means it will be quoted in SQL when used
            # later, no normalizes
            return name

    def denormalize_name(self, name):
        if name is None:
            return None

        name_lower = name.lower()
        name_upper = name.upper()

        if name_upper == name_lower:
            # name has no upper/lower conversion, e.g. non-european characters.
            # return unchanged
            return name
        elif name_lower == name and not (
            self.identifier_preparer._requires_quotes
        )(name_lower):
            name = name_upper
        if util.py2k:
            if not self.supports_unicode_binds:
                name = name.encode(self.encoding)
            else:
                name = unicode(name)  # noqa
        return name


class _RendersLiteral(object):
    def literal_processor(self, dialect):
        def process(value):
            return "'%s'" % value

        return process


class _StrDateTime(_RendersLiteral, sqltypes.DateTime):
    pass


class _StrDate(_RendersLiteral, sqltypes.Date):
    pass


class _StrTime(_RendersLiteral, sqltypes.Time):
    pass


class StrCompileDialect(DefaultDialect):

    statement_compiler = compiler.StrSQLCompiler
    ddl_compiler = compiler.DDLCompiler
    type_compiler = compiler.StrSQLTypeCompiler
    preparer = compiler.IdentifierPreparer

    supports_sequences = True
    sequences_optional = True
    preexecute_autoincrement_sequences = False
    implicit_returning = False

    supports_native_boolean = True

    supports_simple_order_by_label = True

    colspecs = {
        sqltypes.DateTime: _StrDateTime,
        sqltypes.Date: _StrDate,
        sqltypes.Time: _StrTime,
    }


class DefaultExecutionContext(interfaces.ExecutionContext):
    isinsert = False
    isupdate = False
    isdelete = False
    is_crud = False
    is_text = False
    isddl = False
    executemany = False
    compiled = None
    statement = None
    result_column_struct = None
    returned_default_rows = None
    execution_options = util.immutabledict()

    cursor_fetch_strategy = _cursor._DEFAULT_FETCH

    cache_stats = None
    invoked_statement = None

    _is_implicit_returning = False
    _is_explicit_returning = False
    _is_future_result = False
    _is_server_side = False

    _soft_closed = False

    # a hook for SQLite's translation of
    # result column names
    # NOTE: pyhive is using this hook, can't remove it :(
    _translate_colname = None

    _expanded_parameters = util.immutabledict()

    cache_hit = NO_CACHE_KEY

    @classmethod
    def _init_ddl(
        cls,
        dialect,
        connection,
        dbapi_connection,
        execution_options,
        compiled_ddl,
    ):
        """Initialize execution context for a DDLElement construct."""

        self = cls.__new__(cls)
        self.root_connection = connection
        self._dbapi_connection = dbapi_connection
        self.dialect = connection.dialect

        self.compiled = compiled = compiled_ddl
        self.isddl = True

        self.execution_options = execution_options

        self._is_future_result = (
            connection._is_future
            or self.execution_options.get("future_result", False)
        )

        self.unicode_statement = util.text_type(compiled)
        if compiled.schema_translate_map:
            schema_translate_map = self.execution_options.get(
                "schema_translate_map", {}
            )

            rst = compiled.preparer._render_schema_translates
            self.unicode_statement = rst(
                self.unicode_statement, schema_translate_map
            )

        if not dialect.supports_unicode_statements:
            self.statement = dialect._encoder(self.unicode_statement)[0]
        else:
            self.statement = self.unicode_statement

        self.cursor = self.create_cursor()
        self.compiled_parameters = []

        if dialect.positional:
            self.parameters = [dialect.execute_sequence_format()]
        else:
            self.parameters = [{}]

        return self

    @classmethod
    def _init_compiled(
        cls,
        dialect,
        connection,
        dbapi_connection,
        execution_options,
        compiled,
        parameters,
        invoked_statement,
        extracted_parameters,
        cache_hit=CACHING_DISABLED,
    ):
        """Initialize execution context for a Compiled construct."""

        self = cls.__new__(cls)
        self.root_connection = connection
        self._dbapi_connection = dbapi_connection
        self.dialect = connection.dialect
        self.extracted_parameters = extracted_parameters
        self.invoked_statement = invoked_statement
        self.compiled = compiled
        self.cache_hit = cache_hit

        self.execution_options = execution_options

        self._is_future_result = (
            connection._is_future
            or self.execution_options.get("future_result", False)
        )

        self.result_column_struct = (
            compiled._result_columns,
            compiled._ordered_columns,
            compiled._textual_ordered_columns,
            compiled._loose_column_name_matching,
        )
        self.isinsert = compiled.isinsert
        self.isupdate = compiled.isupdate
        self.isdelete = compiled.isdelete
        self.is_text = compiled.isplaintext

        if self.isinsert or self.isupdate or self.isdelete:
            self.is_crud = True
            self._is_explicit_returning = bool(compiled.statement._returning)
            self._is_implicit_returning = bool(
                compiled.returning and not compiled.statement._returning
            )

        if not parameters:
            self.compiled_parameters = [
                compiled.construct_params(
                    extracted_parameters=extracted_parameters
                )
            ]
        else:
            self.compiled_parameters = [
                compiled.construct_params(
                    m,
                    _group_number=grp,
                    extracted_parameters=extracted_parameters,
                )
                for grp, m in enumerate(parameters)
            ]

            self.executemany = len(parameters) > 1

        # this must occur before create_cursor() since the statement
        # has to be regexed in some cases for server side cursor
        if util.py2k:
            self.unicode_statement = util.text_type(compiled.string)
        else:
            self.unicode_statement = compiled.string

        self.cursor = self.create_cursor()

        if self.compiled.insert_prefetch or self.compiled.update_prefetch:
            if self.executemany:
                self._process_executemany_defaults()
            else:
                self._process_executesingle_defaults()

        processors = compiled._bind_processors

        if compiled.literal_execute_params or compiled.post_compile_params:
            if self.executemany:
                raise exc.InvalidRequestError(
                    "'literal_execute' or 'expanding' parameters can't be "
                    "used with executemany()"
                )

            expanded_state = compiled._process_parameters_for_postcompile(
                self.compiled_parameters[0]
            )

            # re-assign self.unicode_statement
            self.unicode_statement = expanded_state.statement

            # used by set_input_sizes() which is needed for Oracle
            self._expanded_parameters = expanded_state.parameter_expansion

            processors = dict(processors)
            processors.update(expanded_state.processors)
            positiontup = expanded_state.positiontup
        elif compiled.positional:
            positiontup = self.compiled.positiontup

        if compiled.schema_translate_map:
            schema_translate_map = self.execution_options.get(
                "schema_translate_map", {}
            )
            rst = compiled.preparer._render_schema_translates
            self.unicode_statement = rst(
                self.unicode_statement, schema_translate_map
            )

        # final self.unicode_statement is now assigned, encode if needed
        # by dialect
        if not dialect.supports_unicode_statements:
            self.statement = self.unicode_statement.encode(
                self.dialect.encoding
            )
        else:
            self.statement = self.unicode_statement

        # Convert the dictionary of bind parameter values
        # into a dict or list to be sent to the DBAPI's
        # execute() or executemany() method.
        parameters = []
        if compiled.positional:
            for compiled_params in self.compiled_parameters:
                param = [
                    processors[key](compiled_params[key])
                    if key in processors
                    else compiled_params[key]
                    for key in positiontup
                ]
                parameters.append(dialect.execute_sequence_format(param))
        else:
            encode = not dialect.supports_unicode_statements
            if encode:
                encoder = dialect._encoder
            for compiled_params in self.compiled_parameters:

                if encode:
                    param = {
                        encoder(key)[0]: processors[key](compiled_params[key])
                        if key in processors
                        else compiled_params[key]
                        for key in compiled_params
                    }
                else:
                    param = {
                        key: processors[key](compiled_params[key])
                        if key in processors
                        else compiled_params[key]
                        for key in compiled_params
                    }

                parameters.append(param)

        self.parameters = dialect.execute_sequence_format(parameters)

        return self

    @classmethod
    def _init_statement(
        cls,
        dialect,
        connection,
        dbapi_connection,
        execution_options,
        statement,
        parameters,
    ):
        """Initialize execution context for a string SQL statement."""

        self = cls.__new__(cls)
        self.root_connection = connection
        self._dbapi_connection = dbapi_connection
        self.dialect = connection.dialect
        self.is_text = True

        self.execution_options = execution_options

        self._is_future_result = (
            connection._is_future
            or self.execution_options.get("future_result", False)
        )

        if not parameters:
            if self.dialect.positional:
                self.parameters = [dialect.execute_sequence_format()]
            else:
                self.parameters = [{}]
        elif isinstance(parameters[0], dialect.execute_sequence_format):
            self.parameters = parameters
        elif isinstance(parameters[0], dict):
            if dialect.supports_unicode_statements:
                self.parameters = parameters
            else:
                self.parameters = [
                    {dialect._encoder(k)[0]: d[k] for k in d}
                    for d in parameters
                ] or [{}]
        else:
            self.parameters = [
                dialect.execute_sequence_format(p) for p in parameters
            ]

        self.executemany = len(parameters) > 1

        if not dialect.supports_unicode_statements and isinstance(
            statement, util.text_type
        ):
            self.unicode_statement = statement
            self.statement = dialect._encoder(statement)[0]
        else:
            self.statement = self.unicode_statement = statement

        self.cursor = self.create_cursor()
        return self

    @classmethod
    def _init_default(
        cls, dialect, connection, dbapi_connection, execution_options
    ):
        """Initialize execution context for a ColumnDefault construct."""

        self = cls.__new__(cls)
        self.root_connection = connection
        self._dbapi_connection = dbapi_connection
        self.dialect = connection.dialect

        self.execution_options = execution_options

        self._is_future_result = (
            connection._is_future
            or self.execution_options.get("future_result", False)
        )

        self.cursor = self.create_cursor()
        return self

    def _get_cache_stats(self):
        if self.compiled is None:
            return "raw sql"

        now = util.perf_counter()

        ch = self.cache_hit

        if ch is NO_CACHE_KEY:
            return "no key %.5fs" % (now - self.compiled._gen_time,)
        elif ch is CACHE_HIT:
            return "cached since %.4gs ago" % (now - self.compiled._gen_time,)
        elif ch is CACHE_MISS:
            return "generated in %.5fs" % (now - self.compiled._gen_time,)
        elif ch is CACHING_DISABLED:
            return "caching disabled %.5fs" % (now - self.compiled._gen_time,)
        else:
            return "unknown"

    @util.memoized_property
    def engine(self):
        return self.root_connection.engine

    @util.memoized_property
    def postfetch_cols(self):
        return self.compiled.postfetch

    @util.memoized_property
    def prefetch_cols(self):
        if self.isinsert:
            return self.compiled.insert_prefetch
        elif self.isupdate:
            return self.compiled.update_prefetch
        else:
            return ()

    @util.memoized_property
    def returning_cols(self):
        self.compiled.returning

    @util.memoized_property
    def no_parameters(self):
        return self.execution_options.get("no_parameters", False)

    @util.memoized_property
    def should_autocommit(self):
        autocommit = self.execution_options.get(
            "autocommit",
            not self.compiled
            and self.statement
            and expression.PARSE_AUTOCOMMIT
            or False,
        )

        if autocommit is expression.PARSE_AUTOCOMMIT:
            return self.should_autocommit_text(self.unicode_statement)
        else:
            return autocommit

    def _execute_scalar(self, stmt, type_, parameters=None):
        """Execute a string statement on the current cursor, returning a
        scalar result.

        Used to fire off sequences, default phrases, and "select lastrowid"
        types of statements individually or in the context of a parent INSERT
        or UPDATE statement.

        """

        conn = self.root_connection
        if (
            isinstance(stmt, util.text_type)
            and not self.dialect.supports_unicode_statements
        ):
            stmt = self.dialect._encoder(stmt)[0]

        if not parameters:
            if self.dialect.positional:
                parameters = self.dialect.execute_sequence_format()
            else:
                parameters = {}

        conn._cursor_execute(self.cursor, stmt, parameters, context=self)
        r = self.cursor.fetchone()[0]
        if type_ is not None:
            # apply type post processors to the result
            proc = type_._cached_result_processor(
                self.dialect, self.cursor.description[0][1]
            )
            if proc:
                return proc(r)
        return r

    @property
    def connection(self):
        conn = self.root_connection
        if conn._is_future:
            return conn
        else:
            return conn._branch()

    def should_autocommit_text(self, statement):
        return AUTOCOMMIT_REGEXP.match(statement)

    def _use_server_side_cursor(self):
        if not self.dialect.supports_server_side_cursors:
            return False

        if self.dialect.server_side_cursors:
            use_server_side = self.execution_options.get(
                "stream_results", True
            ) and (
                (
                    self.compiled
                    and isinstance(
                        self.compiled.statement, expression.Selectable
                    )
                    or (
                        (
                            not self.compiled
                            or isinstance(
                                self.compiled.statement, expression.TextClause
                            )
                        )
                        and self.unicode_statement
                        and SERVER_SIDE_CURSOR_RE.match(self.unicode_statement)
                    )
                )
            )
        else:
            use_server_side = self.execution_options.get(
                "stream_results", False
            )

        return use_server_side

    def create_cursor(self):
        if (
            # inlining initial preference checks for SS cursors
            self.dialect.supports_server_side_cursors
            and (
                self.execution_options.get("stream_results", False)
                or (
                    self.dialect.server_side_cursors
                    and self._use_server_side_cursor()
                )
            )
        ):
            self._is_server_side = True
            return self.create_server_side_cursor()
        else:
            self._is_server_side = False
            return self._dbapi_connection.cursor()

    def create_server_side_cursor(self):
        raise NotImplementedError()

    def pre_exec(self):
        pass

    def get_out_parameter_values(self, names):
        raise NotImplementedError(
            "This dialect does not support OUT parameters"
        )

    def post_exec(self):
        pass

    def get_result_processor(self, type_, colname, coltype):
        """Return a 'result processor' for a given type as present in
        cursor.description.

        This has a default implementation that dialects can override
        for context-sensitive result type handling.

        """
        return type_._cached_result_processor(self.dialect, coltype)

    def get_lastrowid(self):
        """return self.cursor.lastrowid, or equivalent, after an INSERT.

        This may involve calling special cursor functions, issuing a new SELECT
        on the cursor (or a new one), or returning a stored value that was
        calculated within post_exec().

        This function will only be called for dialects which support "implicit"
        primary key generation, keep preexecute_autoincrement_sequences set to
        False, and when no explicit id value was bound to the statement.

        The function is called once for an INSERT statement that would need to
        return the last inserted primary key for those dialects that make use
        of the lastrowid concept.  In these cases, it is called directly after
        :meth:`.ExecutionContext.post_exec`.

        """
        return self.cursor.lastrowid

    def handle_dbapi_exception(self, e):
        pass

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def supports_sane_rowcount(self):
        return self.dialect.supports_sane_rowcount

    def supports_sane_multi_rowcount(self):
        return self.dialect.supports_sane_multi_rowcount

    def _setup_result_proxy(self):
        if self.is_crud or self.is_text:
            result = self._setup_dml_or_text_result()
        else:
            strategy = self.cursor_fetch_strategy
            if self._is_server_side and strategy is _cursor._DEFAULT_FETCH:
                strategy = _cursor.BufferedRowCursorFetchStrategy(
                    self.cursor, self.execution_options
                )
            cursor_description = (
                strategy.alternate_cursor_description
                or self.cursor.description
            )
            if cursor_description is None:
                strategy = _cursor._NO_CURSOR_DQL

            if self._is_future_result:
                result = _cursor.CursorResult(
                    self, strategy, cursor_description
                )
            else:
                result = _cursor.LegacyCursorResult(
                    self, strategy, cursor_description
                )

        if (
            self.compiled
            and not self.isddl
            and self.compiled.has_out_parameters
        ):
            self._setup_out_parameters(result)

        self._soft_closed = result._soft_closed

        return result

    def _setup_out_parameters(self, result):

        out_bindparams = [
            (param, name)
            for param, name in self.compiled.bind_names.items()
            if param.isoutparam
        ]
        out_parameters = {}

        for bindparam, raw_value in zip(
            [param for param, name in out_bindparams],
            self.get_out_parameter_values(
                [name for param, name in out_bindparams]
            ),
        ):

            type_ = bindparam.type
            impl_type = type_.dialect_impl(self.dialect)
            dbapi_type = impl_type.get_dbapi_type(self.dialect.dbapi)
            result_processor = impl_type.result_processor(
                self.dialect, dbapi_type
            )
            if result_processor is not None:
                raw_value = result_processor(raw_value)
            out_parameters[bindparam.key] = raw_value

        result.out_parameters = out_parameters

    def _setup_dml_or_text_result(self):
        if self.isinsert:
            if self.compiled.postfetch_lastrowid:
                self._setup_ins_pk_from_lastrowid()
            elif not self._is_implicit_returning:
                self._setup_ins_pk_from_empty()

        strategy = self.cursor_fetch_strategy
        if self._is_server_side and strategy is _cursor._DEFAULT_FETCH:
            strategy = _cursor.BufferedRowCursorFetchStrategy(
                self.cursor, self.execution_options
            )
        cursor_description = (
            strategy.alternate_cursor_description or self.cursor.description
        )
        if cursor_description is None:
            strategy = _cursor._NO_CURSOR_DML

        if self._is_future_result:
            result = _cursor.CursorResult(self, strategy, cursor_description)
        else:
            result = _cursor.LegacyCursorResult(
                self, strategy, cursor_description
            )

        if self.isinsert:
            if self._is_implicit_returning:
                rows = result.all()

                self.returned_default_rows = rows

                self._setup_ins_pk_from_implicit_returning(result, rows)

                # test that it has a cursor metadata that is accurate. the
                # first row will have been fetched and current assumptions
                # are that the result has only one row, until executemany()
                # support is added here.
                assert result._metadata.returns_rows
                result._soft_close()
            elif not self._is_explicit_returning:
                result._soft_close()

                # we assume here the result does not return any rows.
                # *usually*, this will be true.  However, some dialects
                # such as that of MSSQL/pyodbc need to SELECT a post fetch
                # function so this is not necessarily true.
                # assert not result.returns_rows

        elif self.isupdate and self._is_implicit_returning:
            row = result.fetchone()
            self.returned_default_rows = [row]
            result._soft_close()

            # test that it has a cursor metadata that is accurate.
            # the rows have all been fetched however.
            assert result._metadata.returns_rows

        elif not result._metadata.returns_rows:
            # no results, get rowcount
            # (which requires open cursor on some drivers
            # such as kintersbasdb, mxodbc)
            result.rowcount
            result._soft_close()
        return result

    def _setup_ins_pk_from_lastrowid(self):

        getter = self.compiled._inserted_primary_key_from_lastrowid_getter

        lastrowid = self.get_lastrowid()
        self.inserted_primary_key_rows = [
            getter(lastrowid, self.compiled_parameters[0])
        ]

    def _setup_ins_pk_from_empty(self):

        getter = self.compiled._inserted_primary_key_from_lastrowid_getter

        self.inserted_primary_key_rows = [
            getter(None, param) for param in self.compiled_parameters
        ]

    def _setup_ins_pk_from_implicit_returning(self, result, rows):

        if not rows:
            self.inserted_primary_key_rows = []
            return

        getter = self.compiled._inserted_primary_key_from_returning_getter
        compiled_params = self.compiled_parameters

        self.inserted_primary_key_rows = [
            getter(row, param) for row, param in zip(rows, compiled_params)
        ]

    def lastrow_has_defaults(self):
        return (self.isinsert or self.isupdate) and bool(
            self.compiled.postfetch
        )

    def set_input_sizes(
        self, translate=None, include_types=None, exclude_types=None
    ):
        """Given a cursor and ClauseParameters, call the appropriate
        style of ``setinputsizes()`` on the cursor, using DB-API types
        from the bind parameter's ``TypeEngine`` objects.

        This method only called by those dialects which require it,
        currently cx_oracle.

        """
        if self.isddl:
            return None

        inputsizes = self.compiled._get_set_input_sizes_lookup(
            translate=translate,
            include_types=include_types,
            exclude_types=exclude_types,
        )
        if inputsizes is None:
            return

        if self.dialect._has_events:
            inputsizes = dict(inputsizes)
            self.dialect.dispatch.do_setinputsizes(
                inputsizes, self.cursor, self.statement, self.parameters, self
            )

        if self.dialect.positional:
            positional_inputsizes = []
            for key in self.compiled.positiontup:
                bindparam = self.compiled.binds[key]
                if bindparam in self.compiled.literal_execute_params:
                    continue

                if key in self._expanded_parameters:
                    if bindparam.type._is_tuple_type:
                        num = len(bindparam.type.types)
                        dbtypes = inputsizes[bindparam]
                        positional_inputsizes.extend(
                            [
                                dbtypes[idx % num]
                                for idx, key in enumerate(
                                    self._expanded_parameters[key]
                                )
                            ]
                        )
                    else:
                        dbtype = inputsizes.get(bindparam, None)
                        positional_inputsizes.extend(
                            dbtype for dbtype in self._expanded_parameters[key]
                        )
                else:
                    dbtype = inputsizes[bindparam]
                    positional_inputsizes.append(dbtype)
            try:
                self.cursor.setinputsizes(*positional_inputsizes)
            except BaseException as e:
                self.root_connection._handle_dbapi_exception(
                    e, None, None, None, self
                )
        else:
            keyword_inputsizes = {}
            for bindparam, key in self.compiled.bind_names.items():
                if bindparam in self.compiled.literal_execute_params:
                    continue

                if key in self._expanded_parameters:
                    if bindparam.type._is_tuple_type:
                        num = len(bindparam.type.types)
                        dbtypes = inputsizes[bindparam]
                        keyword_inputsizes.update(
                            [
                                (key, dbtypes[idx % num])
                                for idx, key in enumerate(
                                    self._expanded_parameters[key]
                                )
                            ]
                        )
                    else:
                        dbtype = inputsizes.get(bindparam, None)
                        if dbtype is not None:
                            keyword_inputsizes.update(
                                (expand_key, dbtype)
                                for expand_key in self._expanded_parameters[
                                    key
                                ]
                            )
                else:
                    dbtype = inputsizes.get(bindparam, None)
                    if dbtype is not None:
                        if translate:
                            # TODO: this part won't work w/ the
                            # expanded_parameters feature, e.g. for cx_oracle
                            # quoted bound names
                            key = translate.get(key, key)
                        if not self.dialect.supports_unicode_binds:
                            key = self.dialect._encoder(key)[0]
                        keyword_inputsizes[key] = dbtype
            try:
                self.cursor.setinputsizes(**keyword_inputsizes)
            except BaseException as e:
                self.root_connection._handle_dbapi_exception(
                    e, None, None, None, self
                )

    def _exec_default(self, column, default, type_):
        if default.is_sequence:
            return self.fire_sequence(default, type_)
        elif default.is_callable:
            self.current_column = column
            return default.arg(self)
        elif default.is_clause_element:
            return self._exec_default_clause_element(column, default, type_)
        else:
            return default.arg

    def _exec_default_clause_element(self, column, default, type_):
        # execute a default that's a complete clause element.  Here, we have
        # to re-implement a miniature version of the compile->parameters->
        # cursor.execute() sequence, since we don't want to modify the state
        # of the connection  / result in progress or create new connection/
        # result objects etc.
        # .. versionchanged:: 1.4

        if not default._arg_is_typed:
            default_arg = expression.type_coerce(default.arg, type_)
        else:
            default_arg = default.arg
        compiled = expression.select([default_arg]).compile(
            dialect=self.dialect
        )
        compiled_params = compiled.construct_params()
        processors = compiled._bind_processors
        if compiled.positional:
            positiontup = compiled.positiontup
            parameters = self.dialect.execute_sequence_format(
                [
                    processors[key](compiled_params[key])
                    if key in processors
                    else compiled_params[key]
                    for key in positiontup
                ]
            )
        else:
            parameters = dict(
                (
                    key,
                    processors[key](compiled_params[key])
                    if key in processors
                    else compiled_params[key],
                )
                for key in compiled_params
            )
        return self._execute_scalar(
            util.text_type(compiled), type_, parameters=parameters
        )

    current_parameters = None
    """A dictionary of parameters applied to the current row.

    This attribute is only available in the context of a user-defined default
    generation function, e.g. as described at :ref:`context_default_functions`.
    It consists of a dictionary which includes entries for each column/value
    pair that is to be part of the INSERT or UPDATE statement. The keys of the
    dictionary will be the key value of each :class:`_schema.Column`,
    which is usually
    synonymous with the name.

    Note that the :attr:`.DefaultExecutionContext.current_parameters` attribute
    does not accommodate for the "multi-values" feature of the
    :meth:`_expression.Insert.values` method.  The
    :meth:`.DefaultExecutionContext.get_current_parameters` method should be
    preferred.

    .. seealso::

        :meth:`.DefaultExecutionContext.get_current_parameters`

        :ref:`context_default_functions`

    """

    def get_current_parameters(self, isolate_multiinsert_groups=True):
        """Return a dictionary of parameters applied to the current row.

        This method can only be used in the context of a user-defined default
        generation function, e.g. as described at
        :ref:`context_default_functions`. When invoked, a dictionary is
        returned which includes entries for each column/value pair that is part
        of the INSERT or UPDATE statement. The keys of the dictionary will be
        the key value of each :class:`_schema.Column`,
        which is usually synonymous
        with the name.

        :param isolate_multiinsert_groups=True: indicates that multi-valued
         INSERT constructs created using :meth:`_expression.Insert.values`
         should be
         handled by returning only the subset of parameters that are local
         to the current column default invocation.   When ``False``, the
         raw parameters of the statement are returned including the
         naming convention used in the case of multi-valued INSERT.

        .. versionadded:: 1.2  added
           :meth:`.DefaultExecutionContext.get_current_parameters`
           which provides more functionality over the existing
           :attr:`.DefaultExecutionContext.current_parameters`
           attribute.

        .. seealso::

            :attr:`.DefaultExecutionContext.current_parameters`

            :ref:`context_default_functions`

        """
        try:
            parameters = self.current_parameters
            column = self.current_column
        except AttributeError:
            raise exc.InvalidRequestError(
                "get_current_parameters() can only be invoked in the "
                "context of a Python side column default function"
            )

        compile_state = self.compiled.compile_state
        if (
            isolate_multiinsert_groups
            and self.isinsert
            and compile_state._has_multi_parameters
        ):
            if column._is_multiparam_column:
                index = column.index + 1
                d = {column.original.key: parameters[column.key]}
            else:
                d = {column.key: parameters[column.key]}
                index = 0
            keys = compile_state._dict_parameters.keys()
            d.update(
                (key, parameters["%s_m%d" % (key, index)]) for key in keys
            )
            return d
        else:
            return parameters

    def get_insert_default(self, column):
        if column.default is None:
            return None
        else:
            return self._exec_default(column, column.default, column.type)

    def get_update_default(self, column):
        if column.onupdate is None:
            return None
        else:
            return self._exec_default(column, column.onupdate, column.type)

    def _process_executemany_defaults(self):
        key_getter = self.compiled._key_getters_for_crud_column[2]

        scalar_defaults = {}

        insert_prefetch = self.compiled.insert_prefetch
        update_prefetch = self.compiled.update_prefetch

        # pre-determine scalar Python-side defaults
        # to avoid many calls of get_insert_default()/
        # get_update_default()
        for c in insert_prefetch:
            if c.default and c.default.is_scalar:
                scalar_defaults[c] = c.default.arg
        for c in update_prefetch:
            if c.onupdate and c.onupdate.is_scalar:
                scalar_defaults[c] = c.onupdate.arg

        for param in self.compiled_parameters:
            self.current_parameters = param
            for c in insert_prefetch:
                if c in scalar_defaults:
                    val = scalar_defaults[c]
                else:
                    val = self.get_insert_default(c)
                if val is not None:
                    param[key_getter(c)] = val
            for c in update_prefetch:
                if c in scalar_defaults:
                    val = scalar_defaults[c]
                else:
                    val = self.get_update_default(c)
                if val is not None:
                    param[key_getter(c)] = val

        del self.current_parameters

    def _process_executesingle_defaults(self):
        key_getter = self.compiled._key_getters_for_crud_column[2]
        self.current_parameters = (
            compiled_parameters
        ) = self.compiled_parameters[0]

        for c in self.compiled.insert_prefetch:
            if c.default and not c.default.is_sequence and c.default.is_scalar:
                val = c.default.arg
            else:
                val = self.get_insert_default(c)

            if val is not None:
                compiled_parameters[key_getter(c)] = val

        for c in self.compiled.update_prefetch:
            val = self.get_update_default(c)

            if val is not None:
                compiled_parameters[key_getter(c)] = val
        del self.current_parameters


DefaultDialect.execution_ctx_cls = DefaultExecutionContext
