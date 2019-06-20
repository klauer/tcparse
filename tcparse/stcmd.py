"""
"tcparse-stcmd" is a command line utility for generating ESS/ethercatmc-capable
EPICS startup st.cmd files directly from TwinCAT3 .tsproj projects.

Relies on the existence (and linking) of FB_DriveVirtual function blocks.
"""

import argparse
import getpass
import logging
import pathlib
import sys

import jinja2

try:
    import pytmc
except ImportError:
    pytmc = None
else:
    from pytmc.xml_obj import Configuration as PytmcConfiguration
    from pytmc.bin.pytmc import process as pytmc_process, LinterError

from .parse import load_project, Symbol_FB_DriveVirtual, Property


description = __doc__


def build_arg_parser(parser=None):
    if parser is None:
        parser = argparse.ArgumentParser(
            description=description,
            formatter_class=argparse.RawTextHelpFormatter
        )

    parser.add_argument(
        'tsproj_project', type=str,
        help='Path to .tsproj project'
    )

    parser.add_argument(
        '-p', '--prefix', type=str, default=None,
        help='PV prefix for the IOC'
    )

    parser.add_argument(
        '--binary', type=str, default='adsMotion',
        help='IOC application binary name'
    )

    parser.add_argument(
        '-n', '--name', type=str, default=None,
        help='IOC name (defaults to project name)'
    )

    parser.add_argument(
        '--all-records', default=False, action='store_true',
        help='Parse the TMC file for non-motor records as well'
    )

    parser.add_argument(
        '--db-path', type=str, default='.',
        help='Path for db files'
    )

    parser.add_argument(
        '--dbd', type=str, default=None,
        help='Path to the IOC dbd file'
    )

    parser.add_argument(
        '--delim', type=str, default=':',
        help='Preferred PV delimiter'
    )

    parser.add_argument(
        '--template', type=str, default='stcmd_default.cmd',
        help='st.cmd Jinja2 template',
    )

    parser.add_argument(
        '--log',
        '-l',
        default='INFO',
        type=str,
        help='Python logging level (e.g. DEBUG, INFO, WARNING)'
    )

    return parser


def render_pytmc(tmc_file, *, dbd=None, allow_errors=False,
                 show_error_context=True):
    with open(tmc_file, 'r') as tmc_file:
        tmc_file_obj = pytmc.TmcFile(tmc_file)

    try:
        pytmc_process(
            tmc_file_obj, dbd_file=dbd, allow_errors=allow_errors,
            show_error_context=show_error_context,
        )
    except LinterError:
        sys.exit(1)

    return tmc_file_obj.render()


def render(args):
    logger = logging.getLogger('tcparse')
    logger.setLevel(args.log)

    pytmc_logger = logging.getLogger('pytmc')
    pytmc_logger.setLevel(args.log)
    logging.basicConfig()

    jinja_env = jinja2.Environment(
        loader=jinja2.PackageLoader("tcparse", "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    if not args.name:
        args.name = pathlib.Path(args.tsproj_project).stem

    if not args.prefix:
        args.prefix = args.name.upper()

    template = jinja_env.get_template(args.template)

    project = load_project(args.tsproj_project)
    motors = [(motor, motor.nc_axis)
              for motor in project.find(Symbol_FB_DriveVirtual)]

    def get_pytmc(motor, nc_axis, key):
        '''
        Find a pytmc pragma by key

        Returns
        -------
        value : str or None
            The first value found, if any
        '''
        if pytmc is None:
            return None

        for config in pytmc_info[motor]:
            matches = config.get_config_lines(key)
            if matches:
                return matches[0]['tag']

    def get_name(motor, nc_axis):
        'Returns: (motor_prefix, motor_name)'
        # First check if there is a pytmc pragma
        tmc_name = get_pytmc(motor, nc_axis, 'pv')
        if tmc_name is not None:
            # PV name specified in the pragma - use it as-is
            return '', tmc_name

        # Fall back to using the NC axis name, replacing underscores/spaces
        # with the user-specified delimiter
        name = nc_axis.short_name
        name = name.replace(' ', args.delim)
        return args.prefix + args.delim, name.replace('_', args.delim)

    if pytmc is not None:
        pytmc_info = {
            motor: [PytmcConfiguration(prop.Value[0].text)
                    for prop in motor.find(Property)
                    if prop.name == 'pytmc'
                    ]
            for motor, _ in motors
        }

    template_motors = [
        dict(axisconfig='',
             name=get_name(motor, nc_axis),
             axis_no=nc_axis.axis_number,
             desc=f'{motor.name} / {nc_axis.short_name}',
             egu=nc_axis.units,
             prec=get_pytmc(motor, nc_axis, 'precision') or '3',
             additional_fields=get_pytmc(motor, nc_axis,
                                         'additional_fields') or '',
             amplifier_flags=get_pytmc(motor, nc_axis,
                                       'amplifier_flags') or '0',
             )
        for motor, nc_axis in motors
    ]

    # TODO: for now, only support a single virtual PLC for all motors
    ads_port = motors[0][0].module.ads_port if motors else 851

    additional_db_files = []
    if args.all_records:
        if pytmc is None:
            logger.error('pytmc unavailable; cannot use --all-records.')
            sys.exit(1)

        for proj in project.nested_projects:
            rendered_db = render_pytmc(proj.tmc.filename, dbd=args.dbd)
            if not rendered_db:
                logger.info('No additional records from pytmc found in %s',
                            proj.tmc.filename)
                continue

            db_filename = f'{proj.filename.stem}.db'
            db_path = pathlib.Path(args.db_path) / db_filename
            with open(db_path, 'wt') as db_file:
                db_file.write(rendered_db)
            additional_db_files.append({'file': db_filename, 'macros': ''})

    template_args = dict(
        binary_name=args.binary,
        name=args.name,
        prefix=args.prefix,
        delim=args.delim,
        user=getpass.getuser(),

        motor_port='PLC_ADS',
        asyn_port='ASYN_PLC',
        plc_ams_id=project.ams_id,
        plc_ip=project.target_ip,
        plc_ads_port=ads_port,

        motors=template_motors,
        additional_db_files=additional_db_files,
    )

    return project, motors, template.render(**template_args)


def main(*, cmdline_args=None):
    parser = build_arg_parser()
    _, _, template = render(parser.parse_args(cmdline_args))
    print(template)
