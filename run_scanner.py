#!/usr/bin/python3.7

"""The main script for the scanner unit.

This script should be run automatically after startup (e.g. using crontab). It
will perform the following tasks in order:
    - Check and update the system time using the GPS
    - Connect to the spectrometer and prepare for analysis
    - Wait until the designated start time
    - Connect to the scanner head and find home
    - Scan continuously until the designated stop time
    - Disconnect the scanner head
    - Finish any outstanding analysis
    - Wait to power down, allowing the home station to sync the data as
      required
"""

import os
import sys
import time
import yaml
import logging
from datetime import datetime
from multiprocessing import Process

from ifit.gps import GPS
from ifit.parameters import Parameters
from ifit.spectral_analysis import Analyser
from ifit.spectrometers import Spectrometer

from openso2.scanner import Scanner
from openso2.position import gps_sync
from openso2.analyse_scan import analyse_scan, update_int_time

__version__ = 'v_1_4'

# =============================================================================
# Set up logging
# =============================================================================

# Get the logger
logger = logging.getLogger()

# Setup logger to standard output
logger.setLevel(logging.INFO)
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.INFO)
stdout_formatter = logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S')
stdout_handler.setFormatter(stdout_formatter)
logger.addHandler(stdout_handler)

# Get the date
datestamp = datetime.now().date()

# Create results folder
results_fpath = f'Results/{datestamp}'
if not os.path.exists(f'{results_fpath}/so2/'):
    os.makedirs(f'{results_fpath}/so2/')
if not os.path.exists(f'{results_fpath}/spectra/'):
    os.makedirs(f'{results_fpath}/spectra/')

# Add a file handler o the logger
file_handler = logging.FileHandler(f'{results_fpath}/{datestamp}.log')
log_fmt = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
file_format = logging.Formatter(log_fmt, '%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_format)
logger.addHandler(file_handler)


# =============================================================================
# Set up status log
# =============================================================================

def log_status(status):
    """Log scanner status to file."""
    # Make sure the Station directory exists
    if not os.path.exists('Station'):
        os.makedirs('Station')

    try:
        # Write the current status to the status file
        with open('Station/status.txt', 'w') as w:
            time_str = datetime.now()
            w.write(f'{time_str} - {status}')

    except Exception:
        logger.warning('Failed to update status file', exc_info=True)


# Create handler to log any exceptions
def exception_handler(*exc_info):
    """Handle uncaught exceptions."""
    log_status('Error')
    logger.exception('Uncaught exception!', exc_info=exc_info)


sys.excepthook = exception_handler


# =============================================================================
# Begin the main program
# =============================================================================

def main_loop():
    """Run control loop."""
    log_status('Idle')
    logger.info('Station awake')

# =============================================================================
#   Program setup
# =============================================================================

    # Read in the station operation settings file
    with open('Station/station_settings.yml', 'r') as ymlfile:
        settings = yaml.load(ymlfile, Loader=yaml.FullLoader)
    settings['version'] = __version__

    msg = 'Scanner Settings:'
    for key, value in settings.items():
        msg += f'\n{key}:\t{value}'
    logger.info(msg)

# =============================================================================
#   Sync with GPS
# =============================================================================

    # Connect to the GPS
    gps = GPS()

    # Set a background task to sync the station time and position with the GPS
    p = Process(target=gps_sync, args=[gps, settings['station_name']])
    p.daemon = True
    p.start()

# =============================================================================
#   Connect to the spectrometer
# =============================================================================

    spectro = Spectrometer(
        integration_time=settings['start_int_time'],
        coadds=settings['start_coadds']
    )

# =============================================================================
#   Set up iFit analyser
# =============================================================================

    # Create parameter dictionary
    params = Parameters()

    # Load the parameter information and convert the parameter info to a string
    params_str = 'Fit Parameters\nName\tValue\tVary\tXpath'
    for name, par in settings['fit_parameters'].items():
        par['value'] = float(par['value'])
        params.add(name, **par)
        params_str += f'\n{name}\t{params[name].value}' \
                      f'\t{params[name].vary}\t{params[name].xpath}'
    settings['fit_parameters'] = params_str

    # Generate the analyser
    analyser = Analyser(
        params=params,
        fit_window=[310, 320],
        frs_path='Ref/sao2010.txt',
        stray_flag=True,
        stray_window=[280, 290],
        ils_type='Params',
        ils_path=f'Station/{spectro.serial_number}_ils.txt'
    )

    # Report fitting parameters
    logger.info(params.pretty_print(cols=['name', 'value', 'vary', 'xpath']))

# =============================================================================
#   Begin the scanning loop
# =============================================================================

    start_time = datetime.strptime(settings['start_time'], '%H:%M').time()
    stop_time = datetime.strptime(settings['stop_time'], '%H:%M').time()

    # Create list to hold active processes
    processes = []

    # If before scan time, wait
    if datetime.now().time() < start_time:
        logger.info(f'Station idle, waiting untill {start_time}')

        # Check time every 10s
        while datetime.now().time() < start_time:
            log_status('Idle')
            logger.debug('Station on standby')
            time.sleep(10)

    # Connect to the scanner
    scanner = Scanner(
        switch_pin=settings['switch_pin'],
        step_type=settings['step_type'],
        angle_per_step=settings['angle_per_step'],
        home_angle=settings['home_angle'],
        max_steps_home=settings['max_steps_home'],
        spectrometer=spectro,
        gps=gps
    )
    logger.info('Scanner engaged')

    # Begin loop
    while datetime.now().time() < stop_time:

        # Log status change and scan number
        log_status('Active')
        logger.info(f'Begin scan {scanner.scan_number}')

        # Scan!
        scan_fname = scanner.acquire_scan(settings, results_fpath)

        # Log scan completion
        logger.info(f'Scan {scanner.scan_number} complete')

        # Update the spectrometer integration time
        new_int_time = update_int_time(
            scan_fname, spectro.integration_time, settings
        )
        spectro.update_integration_time(new_int_time)
        logger.info(f'Integration time updated to {int(new_int_time)}')

        # Clear any finished processes from the processes list
        processes = [p for p in processes if p.is_alive()]

        # Check the number of processes. If there are more than two then don't
        #  start another to prevent too many processes running at once
        if len(processes) <= 2:

            # Log the start of the scan analysis
            _, tail = os.path.split(scan_fname)
            logger.info(f'Start analysis for scan {tail}')

            # Build the save filename
            save_fname = f'{results_fpath}/so2/{tail[:-11]}_results.nc'

            # Create new process to handle fitting of the last scan
            p = Process(
                target=analyse_scan,
                args=[scan_fname, analyser, save_fname]
            )

            # Add to array of active processes
            processes.append(p)

            # Begin the process
            p.start()

        else:
            # Log that the process was not started
            logger.warning(
                'Too many processes running, '
                f'scan {scanner.scan_number} not analysed'
            )

        # Update the scan number
        scanner.scan_number += 1

    # Return the scanner to home and release to conserve power
    scanner.find_home()
    scanner.motor.release()
    logger.info('Scanner released')

    # Finish up any analysis that is still ongoing
    for p in processes:
        p.join()

    # Change the station status
    log_status('Asleep')
    logger.info('Station going to sleep')


if __name__ == '__main__':
    main_loop()
