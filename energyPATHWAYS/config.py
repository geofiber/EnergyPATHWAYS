__author__ = 'Ben Haley & Ryan Jones'

import os
import errno
import ConfigParser
import pint
import geomapper
from .util import splitclean, csv_read_table, create_weibul_coefficient_of_variation, upper_dict, ensure_iterable
import warnings
import pandas as pd
from collections import defaultdict
import datetime
import logging
import sys
from csvdb.data_object import str_to_id
from pyomo.opt import SolverFactory
import pdb
import os
import platform
from pkg_resources import resource_string
from error import ConfigFileError, PathwaysException
from energyPATHWAYS.generated.new_database import EnergyPathwaysDatabase
import unit_converter
from csvdb.database import CsvMetadata

# Don't print warnings
warnings.simplefilter("ignore")

# core inputs
workingdir = None
weibul_coeff_of_var = None

# pickle names
full_model_append_name = '_full_model.p'
demand_model_append_name = '_demand_model.p'
model_error_append_name = '_model_error.p'

# common data inputs
index_levels = None
dnmtr_col_names = ['driver_denominator_1', 'driver_denominator_2']
drivr_col_names = ['driver_1', 'driver_2']
tech_classes = ['capital_cost_new', 'capital_cost_replacement', 'installation_cost_new', 'installation_cost_replacement', 'fixed_om', 'variable_om', 'efficiency']
#storage techs have additional attributes specifying costs for energy (i.e. kWh of energy storage) and discharge capacity (i.e. kW)
storage_tech_classes = ['installation_cost_new','installation_cost_replacement', 'fixed_om', 'variable_om', 'efficiency', 'capital_cost_new_capacity', 'capital_cost_replacement_capacity',
                        'capital_cost_new_energy', 'capital_cost_replacement_energy']

# Initiate pint for unit conversions
calculation_energy_unit = None

# run years
years = None
supply_years = None

# shapes
shape_start_date = None
shape_years = None
date_lookup = None
time_slice_col = None
electricity_energy_type = None
elect_default_shape_key = None
opt_period_length = None
solver_name = None
transmission_constraint = None
filter_dispatch_less_than_x = None

# outputs
output_levels = None
currency_name = None
output_energy_unit = None
output_currency = None
output_demand_levels = None
output_supply_levels = None
output_combined_levels = None
outputs_id_map = defaultdict(dict)
dispatch_write_years = None
timestamp = None

# parallel processing
available_cpus = None

#logging
log_name = None

def initialize_config():
    global weibul_coeff_of_var, available_cpus, cfgfile_name, log_name, log_initialized, index_levels, solver_name, timestamp
    global years, supply_years, workingdir
    workingdir = os.getcwd()

    years = range(getParamAsInt( 'demand_start_year'),
                   getParamAsInt( 'end_year') + 1,
                   getParamAsInt( 'year_step'))

    supply_years = range(getParamAsInt( 'current_year'),
                          getParamAsInt( 'end_year') + 1,
                          getParamAsInt( 'year_step'))

    log_name = '{} energyPATHWAYS log.log'.format(str(datetime.datetime.now())[:-4].replace(':', '.'))
    setuplogging()

    init_db()
    init_units()
    geomapper.GeoMapper()
    init_date_lookup()
    init_output_parameters()
    unit_converter.UnitConverter.get_instance()
    # used when reading in raw_values from data tables
    index_levels = csv_read_table('IndexLevels', column_names=['index_level', 'data_column_name'])
    solver_name = find_solver()

    available_cpus = getParamAsInt('num_cores')
    weibul_coeff_of_var = create_weibul_coefficient_of_variation()
    timestamp = str(datetime.datetime.now().replace(second=0,microsecond=0))

def setuplogging():
    if not os.path.exists(os.path.join(os.getcwd(), 'logs')):
        os.makedirs(os.path.join(os.getcwd(), 'logs'))
    log_path = os.path.join(os.getcwd(), 'logs', log_name)
    log_level = getParam('log_level', section='log').upper()
    logging.basicConfig(filename=log_path, level=log_level)
    logger = logging.getLogger()
    if getParamAsBoolean('stdout', 'log') and not any(type(h) is logging.StreamHandler for h in logger.handlers):
        soh = logging.StreamHandler(sys.stdout)
        soh.setLevel(log_level)
        logger.addHandler(soh)

def init_db():
    dbdir = getParam('database_path')
    EnergyPathwaysDatabase.get_database(dbdir, load=False)

def init_units():
    # Initiate pint for unit conversions
    global calculation_energy_unit

    calculation_energy_unit = getParam('calculation_energy_unit')


def init_date_lookup():
    global date_lookup, time_slice_col, electricity_energy_type, electricity_energy_type_shape, opt_period_length, transmission_constraint, filter_dispatch_less_than_x, elect_default_shape_key
    time_slice_col = ['year', 'month', 'week', 'hour', 'day_type']

    # electricity_energy_type_shape = csv_read_table('FinalEnergy', column_names=['shape'], name='electricity')
    electricity_energy_type = 'electricity'
    elect_default_shape_key = csv_read_table('FinalEnergy', column_names=['shape'], name='electricity')

    opt_period_length = getParamAsInt('period_length', 'opt')
    transmission_constraint = _ConfigParser.get('opt','transmission_constraint')
    transmission_constraint = int(transmission_constraint) if transmission_constraint != "" else None
    filter_dispatch_less_than_x = _ConfigParser.get('output_detail','filter_dispatch_less_than_x')
    filter_dispatch_less_than_x = float(filter_dispatch_less_than_x) if filter_dispatch_less_than_x != "" else None

def init_removed_levels():
    global removed_demand_levels
    removed_demand_levels = splitclean(_ConfigParser.get('removed_levels', 'levels'))

def init_output_levels():
    global output_demand_levels, output_supply_levels, output_combined_levels
    output_demand_levels = ['year', 'vintage', 'demand_technology', geomapper.GeoMapper.demand_primary_geography, 'sector', 'subsector', 'final_energy','other_index_1','other_index_2','cost_type','new/replacement']
    output_supply_levels = ['year', 'vintage', 'supply_technology', geomapper.GeoMapper.supply_primary_geography,  'demand_sector', 'supply_node', 'ghg', 'resource_bin','cost_type']
    output_combined_levels = list(set(output_supply_levels + output_demand_levels + [geomapper.GeoMapper.combined_outputs_geography + "_supply"]))
    output_combined_levels = list(set(output_combined_levels) - {geomapper.GeoMapper.demand_primary_geography, geomapper.GeoMapper.supply_primary_geography}) + [geomapper.GeoMapper.combined_outputs_geography]

    for x in [x[0] for x in _ConfigParser.items('demand_output_detail')]:
        if x in output_demand_levels and _ConfigParser.get('demand_output_detail', x).lower() != 'true':
            output_demand_levels.remove(x)
    for x in [x[0] for x in _ConfigParser.items('supply_output_detail')]:
        if x in output_supply_levels and _ConfigParser.get('supply_output_detail',x).lower() != 'true':
            output_supply_levels.remove(x)
    for x in [x[0] for x in _ConfigParser.items('combined_output_detail')]:
        if _ConfigParser.get('combined_output_detail',x).lower() != 'true':
            if x == 'supply_geography':
                x = geomapper.GeoMapper.combined_outputs_geography + "_supply"
            if x in output_combined_levels:
                output_combined_levels.remove(x)

def table_dict(table_name, columns=['id', 'name'], append=False,
               other_index_id=id, return_iterable=False, return_unique=True):
    df = csv_read_table(table_name, columns,
                        other_index_id=other_index_id,
                        return_iterable=return_iterable,
                        return_unique=return_unique)

    result = upper_dict(df, append=append)
    return result

# def init_outputs_id_map():
#     global outputs_id_map
#
#     demand_primary_geography = geo.get_demand_primary_geography_name()
#     supply_primary_geography = geo.get_supply_primary_geography_name()
#     dispatch_geography_name = geo.get_dispatch_geography_name()
#
#     geo_names = geo.geography_names.items()
#
#     outputs_id_map[demand_primary_geography] = upper_dict(geo_names)
#     outputs_id_map[supply_primary_geography] = upper_dict(geo_names)
#     outputs_id_map[supply_primary_geography + "_supply"] = upper_dict(geo_names)
#     outputs_id_map[supply_primary_geography + "_input"]  = upper_dict(geo_names)
#     outputs_id_map[supply_primary_geography + "_output"] = upper_dict(geo_names)
#     outputs_id_map[demand_primary_geography + "_input"]  = upper_dict(geo_names)
#     outputs_id_map[demand_primary_geography + "_output"] = upper_dict(geo_names)
#     outputs_id_map[dispatch_geography_name] = upper_dict(geo_names)
#
#     outputs_id_map['demand_technology'] = table_dict('DemandTechs')
#     outputs_id_map['supply_technology'] = table_dict('SupplyTechs')
#     outputs_id_map['final_energy'] = table_dict('FinalEnergy')
#     outputs_id_map['supply_node'] = table_dict('SupplyNodes')
#     outputs_id_map['blend_node'] = table_dict('SupplyNodes')
#     outputs_id_map['input_node'] = table_dict('SupplyNodes')
#     outputs_id_map['supply_node_output'] = outputs_id_map['supply_node']
#     outputs_id_map['supply_node_input'] = outputs_id_map['supply_node']
#     outputs_id_map['supply_node_export'] = table_dict('SupplyNodes', append=True) # " EXPORT")  ? Why was this passed as a boolean parameter?
#     outputs_id_map['subsector'] = table_dict('DemandSubsectors')
#     outputs_id_map['demand_sector'] = table_dict('DemandSectors')
#     outputs_id_map['sector'] = outputs_id_map['demand_sector']
#     outputs_id_map['ghg'] = table_dict('GreenhouseGases')
#     outputs_id_map['driver'] = table_dict('DemandDrivers')
#     outputs_id_map['dispatch_feeder'] = table_dict('DispatchFeeders')
#     outputs_id_map['dispatch_feeder'][0] = 'BULK'
#     outputs_id_map['other_index_1'] = table_dict('OtherIndexesData')
#     outputs_id_map['other_index_2'] = table_dict('OtherIndexesData')
#     outputs_id_map['timeshift_type'] = table_dict('FlexibleLoadShiftTypes')
#
#     for id, name in csv_read_table('OtherIndexes', ('id', 'name'), return_iterable=True):
#         if name in ('demand_technology', 'final_energy'):
#             continue
#         outputs_id_map[name] = table_dict('OtherIndexesData', other_index_id=id, return_unique=True)

def init_output_parameters():
    global currency_name, output_currency, output_tco, output_payback, evolved_run, evolved_blend_nodes, evolved_years,\
    rio_supply_run, rio_geography, rio_feeder_geographies, rio_energy_unit, rio_time_unit, rio_timestep_multiplier, rio_zonal_blend_nodes, rio_excluded_technologies, rio_excluded_blends, rio_export_blends, rio_no_negative_blends

    currency_name = getParam('currency_name')
    output_currency = getParam('currency_year') + ' ' + currency_name
    output_tco = getParamAsBoolean('output_tco', section='output_detail')
    output_payback = getParamAsBoolean('output_payback', section='output_detail')
    rio_supply_run = getParamAsBoolean('rio_supply_run', section='rio')
    rio_geography = getParam('rio_geography', section='rio')
    rio_feeder_geographies = [feeder_geo.strip() for feeder_geo in getParam('rio_feeder_geographies', section='rio').split(',')]
    rio_energy_unit = getParam('rio_energy_unit', section='rio')
    rio_time_unit = getParam('rio_time_unit', section='rio')
    rio_timestep_multiplier = getParamAsInt('rio_timestep_multiplier', section='rio')
    # todo: these aren't going to be integers
    rio_zonal_blend_nodes = [int(g) for g in _ConfigParser.get('rio', 'rio_zonal_blends').split(',') if len(g)]
    rio_excluded_technologies = [int(g) for g in _ConfigParser.get('rio', 'rio_excluded_technologies').split(',') if len(g)]
    rio_excluded_blends = [int(g) for g in _ConfigParser.get('rio', 'rio_excluded_blends').split(',') if len(g)]
    rio_export_blends = [int(g) for g in _ConfigParser.get('rio', 'rio_export_blends').split(',') if len(g)]
    rio_no_negative_blends = [int(g) for g in _ConfigParser.get('rio', 'rio_no_negative_blends').split(',') if len(g)]
    evolved_run = _ConfigParser.get('evolved','evolved_run').lower()
    evolved_years = [int(x) for x in ensure_iterable(_ConfigParser.get('evolved', 'evolved_years'))]
    evolved_blend_nodes = splitclean(_ConfigParser.get('evolved','evolved_blend_nodes'), as_type=int)
    init_removed_levels()
    init_output_levels()
    # init_outputs_id_map()

def find_solver():
    dispatch_solver = _ConfigParser.get('opt', 'dispatch_solver')
    # TODO: is replacing spaces just stripping surrounding whitespace? If so, use splitclean instead
    requested_solvers = _ConfigParser.get('opt', 'dispatch_solver').replace(' ', '').split(',')
    solver_name = None
    # inspired by the solver detection code at https://software.sandia.gov/trac/pyomo/browser/pyomo/trunk/pyomo/scripting/driver_help.py#L336
    # suppress logging of warnings for solvers that are not found
    logger = logging.getLogger('pyomo.solvers')
    _level = logger.getEffectiveLevel()
    logger.setLevel(logging.ERROR)
    for requested_solver in requested_solvers:
        logging.debug("Looking for %s solver" % requested_solver)
        if SolverFactory(requested_solver).available(False):
            solver_name = requested_solver
            logging.debug("Using %s solver" % requested_solver)
            break
    # restore logging
    logger.setLevel(_level)

    assert solver_name is not None, "Dispatch could not find any of the solvers requested in your configuration (%s) please see README.md, check your configuration, and make sure you have at least one requested solver installed." % ', '.join(requested_solvers)
    return solver_name

# #
# # TODO: spoof the new config objects' API, with values appearing in [DEFAULT]
# #
# from .error import ConfigFileError
#
# def getParam(name, section=None):
#     value = cfgfile.get(section or 'DEFAULT', name)
#     return value
#
# _True  = ['t', 'y', 'true',  'yes', 'on',  '1']
# _False = ['f', 'n', 'false', 'no',  'off', '0']
#
# def stringTrue(value, raiseError=True):
#     value = str(value).lower()
#
#     if value in _True:
#         return True
#
#     if value in _False:
#         return False
#
#     if raiseError:
#         msg = 'Unrecognized boolean value: "{}". Must one of {}'.format(value, _True + _False)
#         raise ConfigFileError(msg)
#     else:
#         return None
#
# def getParamAsBoolean(name, section=None):
#     """
#     Get the value of the configuration parameter `name`, coerced
#     into a boolean value, where any (case-insensitive) value in the
#     set ``{'true','yes','on','1'}`` are converted to ``True``, and
#     any value in the set ``{'false','no','off','0'}`` is converted to
#     ``False``. Any other value raises an exception.
#     Calls :py:func:`getConfig` if needed.
#
#     :param name: (str) the name of a configuration parameters.
#     :param section: (str) the name of the section to read from, which
#       defaults to the value used in the first call to ``getConfig``,
#       ``readConfigFiles``, or any of the ``getParam`` variants.
#     :return: (bool) the value of the variable
#     :raises: :py:exc:`rio.error.ConfigFileError`
#     """
#     value = getParam(name, section=section)
#     result = stringTrue(value, raiseError=False)
#
#     if result is None:
#         msg = 'The value of variable "{}", {}, could not converted to boolean.'.format(name, value)
#         raise ConfigFileError(msg)
#
#     return result
#
#
# def getParamAsInt(name, section=None):
#     """
#     Get the value of the configuration parameter `name`, coerced
#     to an integer. Calls :py:func:`getConfig` if needed.
#
#     :param name: (str) the name of a configuration parameters.
#     :param section: (str) the name of the section to read from, which
#       defaults to the value used in the first call to ``getConfig``,
#       ``readConfigFiles``, or any of the ``getParam`` variants.
#     :return: (int) the value of the variable
#     """
#     value = getParam(name, section=section)
#     return int(value)
#
#
# def getParamAsFloat(name, section=None):
#     """
#     Get the value of the configuration parameter `name` as a
#     float. Calls :py:func:`getConfig` if needed.
#
#     :param name: (str) the name of a configuration parameters.
#     :param section: (str) the name of the section to read from, which
#       defaults to the value used in the first call to ``getConfig``,
#       ``readConfigFiles``, or any of the ``getParam`` variants.
#     :return: (float) the value of the variable
#     """
#     value = getParam(name, section=section)
#     return float(value)


DEFAULT_SECTION = 'DEFAULT'
PROJ_CONFIG_FILE = 'config.ini'

PlatformName = platform.system()

_ConfigParser = None

_ProjectSection = DEFAULT_SECTION


_ReadUserConfig = False

def getSection():
    return _ProjectSection

def configLoaded():
    return bool(_ConfigParser)

def getConfig(reload=False):
    if reload:
        global _ConfigParser
        _ConfigParser = None

    return _ConfigParser or readConfigFiles()

def readConfigFiles():
    global _ConfigParser

    _ConfigParser = ConfigParser.ConfigParser()
    config_path = os.path.join(os.getcwd(), PROJ_CONFIG_FILE)

    if not os.path.isfile(config_path):
        raise IOError(errno.ENOENT, "Unable to load configuration file. "
                                    "Please make sure your configuration file is located at {}, "
                                    "or use the -p and -c command line options to specify a different location. "
                                    "Type `energyPATHWAYS --help` for help on these options.".format(str(config_path)))

    _ConfigParser.read(config_path)

    return _ConfigParser


def setParam(name, value, section=None):
    """
    Set a configuration parameter in memory.

    :param name: (str) parameter name
    :param value: (any, coerced to str) parameter value
    :param section: (str) if given, the name of the section in which to set the value.
       If not given, the value is set in the established project section, or DEFAULT
       if no project section has been set.
    :return: value
    """
    section = section or getSection()

    if not _ConfigParser:
        getConfig()

    _ConfigParser.set(section, name, value)
    return value

def getParam(name, section=None, raw=False, raiseError=True):
    """
    Get the value of the configuration parameter `name`. Calls
    :py:func:`getConfig` if needed.

    :param name: (str) the name of a configuration parameters. Note
       that variable names are case-insensitive. Note that environment
       variables are available using the '$' prefix as in a shell.
       To access the value of environment variable FOO, use getParam('$FOO').

    :param section: (str) the name of the section to read from, which
      defaults to the value used in the first call to ``getConfig``,
      ``readConfigFiles``, or any of the ``getParam`` variants.
    :return: (str) the value of the variable, or None if the variable
      doesn't exist and raiseError is False.
    :raises NoOptionError: if the variable is not found in the given
      section and raiseError is True
    """
    section = section or getSection()

    if not section:
        raise PathwaysException('getParam was called without setting "section"')

    if not _ConfigParser:
        getConfig()

    try:
        value = _ConfigParser.get(section, name, raw=raw)

    except ConfigParser.NoSectionError:
        if raiseError:
            pdb.set_trace()
            raise PathwaysException('getParam: unknown section "%s"' % section)
        else:
            return None

    except ConfigParser.NoOptionError:
        if raiseError:
            raise PathwaysException('getParam: unknown variable "%s"' % name)
        else:
            return None

    return value

_True  = ['t', 'y', 'true',  'yes', 'on',  '1']
_False = ['f', 'n', 'false', 'no',  'off', '0']

def stringTrue(value, raiseError=True):
    value = str(value).lower()

    if value in _True:
        return True

    if value in _False:
        return False

    if raiseError:
        msg = 'Unrecognized boolean value: "{}". Must one of {}'.format(value, _True + _False)
        raise ConfigFileError(msg)
    else:
        return None


def getParamAsBoolean(name, section=None):
    """
    Get the value of the configuration parameter `name`, coerced
    into a boolean value, where any (case-insensitive) value in the
    set ``{'true','yes','on','1'}`` are converted to ``True``, and
    any value in the set ``{'false','no','off','0'}`` is converted to
    ``False``. Any other value raises an exception.
    Calls :py:func:`getConfig` if needed.

    :param name: (str) the name of a configuration parameters.
    :param section: (str) the name of the section to read from, which
      defaults to the value used in the first call to ``getConfig``,
      ``readConfigFiles``, or any of the ``getParam`` variants.
    :return: (bool) the value of the variable
    :raises: :py:exc:`rio.error.ConfigFileError`
    """
    value = getParam(name, section=section)
    result = stringTrue(value, raiseError=False)

    if result is None:
        msg = 'The value of variable "{}", {}, could not converted to boolean.'.format(name, value)
        raise ConfigFileError(msg)

    return result


def getParamAsInt(name, section=None):
    """
    Get the value of the configuration parameter `name`, coerced
    to an integer. Calls :py:func:`getConfig` if needed.

    :param name: (str) the name of a configuration parameters.
    :param section: (str) the name of the section to read from, which
      defaults to the value used in the first call to ``getConfig``,
      ``readConfigFiles``, or any of the ``getParam`` variants.
    :return: (int) the value of the variable
    """
    value = getParam(name, section=section)
    return int(value)

def getParamAsFloat(name, section=None):
    """
    Get the value of the configuration parameter `name` as a
    float. Calls :py:func:`getConfig` if needed.

    :param name: (str) the name of a configuration parameters.
    :param section: (str) the name of the section to read from, which
      defaults to the value used in the first call to ``getConfig``,
      ``readConfigFiles``, or any of the ``getParam`` variants.
    :return: (float) the value of the variable
    """
    value = getParam(name, section=section)
    return float(value)

def getParamAsString(name, section=None):
    """
    Get the value of the configuration parameter `name` as a
    string. Calls :py:func:`getConfig` if needed.

    :param name: (str) the name of a configuration parameters.
    :param section: (str) the name of the section to read from, which
      defaults to the value used in the first call to ``getConfig``,
      ``readConfigFiles``, or any of the ``getParam`` variants.
    :return: (string) the value of the variable
    """
    value = getParam(name, section=section)
    return value
