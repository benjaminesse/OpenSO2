"""Useful functions for the GUI."""

import os
import sys
import logging
import traceback
import numpy as np
import xarray as xr
import pandas as pd
from scipy.signal import savgol_filter
from datetime import datetime, timedelta
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt, QObject, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QTextEdit, QLineEdit, QDoubleSpinBox,
                             QSpinBox, QCheckBox, QDateTimeEdit, QDateEdit,
                             QPlainTextEdit, QFileDialog)

from openso2.plume import calc_plume_altitude, calc_scan_flux


logger = logging.getLogger(__name__)


# =============================================================================
# Logging text box
# =============================================================================

class QTextEditLogger(logging.Handler, QObject):
    """Record logs to the GUI."""

    appendPlainText = pyqtSignal(str)

    def __init__(self, parent):
        """Initialise."""
        super().__init__()
        QObject.__init__(self)
        self.widget = QPlainTextEdit(parent)
        self.widget.setReadOnly(True)
        self.widget.setFont(QFont('Courier', 10))
        self.appendPlainText.connect(self.widget.appendPlainText)

    def emit(self, record):
        """Emit the log."""
        msg = self.format(record)
        self.appendPlainText.emit(msg)


# =============================================================================
# Station Sync Worker
# =============================================================================

class SyncWorker(QObject):
    """Handle station syncing."""

    # Define signals
    error = pyqtSignal(tuple)
    finished = pyqtSignal()
    updateLog = pyqtSignal(str, list)
    updateStationStatus = pyqtSignal(str, str, str)
    updateGuiStatus = pyqtSignal(str)
    updatePlots = pyqtSignal(str, str)
    updateFluxPlot = pyqtSignal(str)

    def __init__(self, res_dir, stations, analysis_date, sync_mode, volc_loc,
                 default_alt, default_az, wind_speed, scan_pair_time,
                 scan_pair_flag, min_scd, max_scd, min_int, max_int):
        """Initialize."""
        super(QObject, self).__init__()
        self.res_dir = res_dir
        self.stations = stations
        self.analysis_date = analysis_date
        self.sync_mode = sync_mode
        self.volc_loc = volc_loc
        self.default_alt = default_alt
        self.default_az = default_az
        self.wind_speed = wind_speed
        self.scan_pair_time = scan_pair_time
        self.scan_pair_flag = scan_pair_flag
        self.min_scd = min_scd
        self.max_scd = max_scd
        self.min_int = min_int
        self.max_int = max_int

    def run(self):
        """Launch worker task."""
        try:
            self._run()
        except Exception:
            traceback.print_exc()
            exctype, value = sys.exc_info()[:2]
            self.error.emit((exctype, value, traceback.format_exc()))
        self.finished.emit()

    def _run(self):
        """Sync the station logs and scans."""
        # Generate an empty dictionary to hold the scans
        scans = {}

        # Set the file path to the results folder
        fpath = f'{self.res_dir}/{self.analysis_date}'

        # Sync each station
        for station in self.stations.values():

            if not station.sync_flag:
                logging.info(f'Syncing {station.name} station disabled')
                continue

            logging.info(f'Syncing {station.name} station...')

            stat_dir = f'{self.res_dir}/{self.analysis_date}/{station.name}/'
            if not os.path.isdir(stat_dir):
                os.makedirs(stat_dir)

            # Sync the station status and log
            time, status, err = station.pull_status()

            # Update the station status
            self.updateStationStatus.emit(station.name, time, status)

            # If the connection fails, skip
            if err[0]:
                logger.info(f'Connection to {station.name} failed')
                continue

            # Pull the station logs
            fname, err = station.pull_log(local_dir=self.res_dir)

            # Read the log file
            if fname is not None:
                with open(fname, 'r') as r:
                    log_text = r.readlines()

                # Send signal with log text
                self.updateLog.emit(station.name, log_text)

            # Sync spectra files
            if self.sync_mode in ['spec', 'both']:
                local_dir = f'{self.res_dir}/{self.analysis_date}/' \
                            + f'{station.name}/spectra/'
                if not os.path.isdir(local_dir):
                    os.makedirs(local_dir)
                remote_dir = '/home/pi/OpenSO2/Results/' \
                             + f'{self.analysis_date}/spectra/'
                new_spec_fnames, err = station.sync(local_dir, remote_dir)
                logging.info(f'Synced {len(new_spec_fnames)} spectra scans '
                             + f'from {station.name}')

            # Sync so2 files
            if self.sync_mode in ['so2', 'both']:
                local_dir = f'{self.res_dir}/{self.analysis_date}/' \
                            + f'{station.name}/so2/'
                if not os.path.isdir(local_dir):
                    os.makedirs(local_dir)
                remote_dir = '/home/pi/OpenSO2/Results/' \
                             + f'{self.analysis_date}/so2/'
                new_so2_fnames, err = station.sync(local_dir, remote_dir)
                logging.info(f'Synced {len(new_so2_fnames)} scans from '
                             + f'{station.name}')

                # Add the scans to the dictionary
                scans[station.name] = new_so2_fnames

                # Update scan plots if new data is found
                self.updatePlots.emit(station.name, fpath)

        # Get all local files to recalculate flux with updated scans
        all_scans, scan_times = get_local_scans(self.stations, fpath)

        nscans = np.array([len(s) for s in scans.values()])

        # Calculate the fluxes if there are any new so2 scans
        if nscans.any():
            self.updateGuiStatus.emit('Calculating fluxes')
            flux_results = calculate_fluxes(
                self.stations, all_scans, fpath, self.volc_loc,
                self.default_alt, self.default_az, self.wind_speed,
                self.scan_pair_time, self.scan_pair_flag, self.min_scd,
                self.max_scd, self.min_int, self.max_int)

            # Format the file name of the flux output file
            for name, flux_df in flux_results.items():
                try:
                    flux_df.to_csv(f'{fpath}/{name}/{self.analysis_date}_'
                                   + f'{name}_fluxes.csv')
                except FileNotFoundError:
                    pass

            # Plot the fluxes on the GUI
            self.updateFluxPlot.emit('RealTime')

        self.updateGuiStatus.emit('Ready')


# =============================================================================
# Post Analysis Worker
# =============================================================================

class PostAnalysisWorker(QObject):
    """Handle flux post analysis."""

    # Define signals
    error = pyqtSignal(tuple)
    finished = pyqtSignal()
    updateGuiStatus = pyqtSignal(str)
    updateFluxPlot = pyqtSignal(str)
    updatePlots = pyqtSignal(str, str)

    def __init__(self, stations, resfpath, date_to_analyse, volc_loc,
                 default_alt, default_az, wind_speed, scan_pair_time,
                 scan_pair_flag, min_scd, max_scd, min_int, max_int):
        """Initialize."""
        super(QObject, self).__init__()
        self.stations = stations
        self.resfpath = resfpath
        self.date_to_analyse = date_to_analyse
        self.volc_loc = volc_loc
        self.default_alt = default_alt
        self.default_az = default_az
        self.wind_speed = wind_speed
        self.scan_pair_time = scan_pair_time
        self.scan_pair_flag = scan_pair_flag
        self.min_scd = min_scd
        self.max_scd = max_scd
        self.min_int = min_int
        self.max_int = max_int

    def run(self):
        """Launch worker task."""
        try:
            self._run()
        except Exception:
            traceback.print_exc()
            exctype, value = sys.exc_info()[:2]
            self.error.emit((exctype, value, traceback.format_exc()))
        self.finished.emit()

    def _run(self):
        """Calculate fluxes from locally stored scans."""
        # Get all local files to recalculate flux with updated scans
        fpath = f'{self.resfpath}/{self.date_to_analyse}'
        all_scans, scan_times = get_local_scans(self.stations, fpath)

        for name, station in self.stations.items():
            self.updatePlots.emit(name, fpath)  # all_scans[name][-1])

        # Calculate the fluxes
        self.updateGuiStatus.emit('Calculating fluxes')

        flux_results = calculate_fluxes(self.stations, all_scans, fpath,
                                        self.volc_loc, self.default_alt,
                                        self.default_az, self.wind_speed,
                                        self.scan_pair_time,
                                        self.scan_pair_flag, self.min_scd,
                                        self.max_scd, self.min_int,
                                        self.max_int)

        # Format the file name of the flux output file
        for name, flux_df in flux_results.items():
            try:
                flux_df.to_csv(
                    f'{fpath}/{name}/{self.date_to_analyse}_{name}_'
                    'fluxes_reanalysed.csv'
                )
            except FileNotFoundError:
                pass

        # Plot the fluxes on the GUI
        self.updateFluxPlot.emit('Post')

        self.updateGuiStatus.emit('Ready')


def calculate_fluxes(stations, scans, fpath, vent_loc, default_alt, default_az,
                     wind_speed, scan_pair_time, scan_pair_flag, min_scd=-1e17,
                     max_scd=1e20, min_int=500, max_int=60000, plume_scd=1e17,
                     good_scan_lim=0.2, sg_window=11, sg_polyn=3):
    """Calculate the flux from a set of scans."""
    # Get the existing scan database
    scan_fnames, scan_times = get_local_scans(stations, fpath)

    # Create a dictionary to hold the flux results
    flux_results = {}

    # For each station calculate fluxes
    for name, station in stations.items():

        logger.info(f'Calculating fluxes for {name}')

        cols = [
            'Time [UTC]', 'Scan File', 'Pair Station', 'Pair File',
            'Flux [kg/s]', 'Flux Err [kg/s]', 'Plume Altitude [m]',
            'Plume Direction [deg]', 'Wind Speed [m/s]']
        flux_df = pd.DataFrame(index=np.arange(len(scans[name])), columns=cols)

        for i, scan_fname in enumerate(scans[name]):

            # Read in the scan
            with xr.open_dataset(scan_fname) as scan_da:

                # Filter the scan
                msk_scan_da, peak, msg = filter_scan(
                    scan_da, min_scd, max_scd, min_int, max_int, plume_scd,
                    good_scan_lim, sg_window, sg_polyn
                )

            # Pull the scan time from the filename
            scan_time = datetime.strptime(os.path.split(scan_fname)[1][:14],
                                          '%Y%m%d_%H%M%S')

            if msk_scan_da is None:
                logger.info(f'Scan {scan_fname} not analysed. {msg}')
                row = [scan_time, os.path.split(scan_fname)[1], None, None,
                       None, None, None, None, None]
                flux_df.iloc[i] = row
                continue

            if scan_pair_flag:
                # Find the nearest scan from other stations
                near_fname, near_ts, alt_name = find_nearest_scan(
                    name, scan_time, scan_fnames, scan_times
                )
            else:
                near_fname, near_ts, alt_name = None, None, None

            if near_fname is None:
                near_fname = 'None'
                alt_station_name = None
                plume_alt = default_alt
                plume_az = default_az

            else:
                # Calculate the time difference
                time_diff = scan_time - near_ts
                delta_time = timedelta(minutes=scan_pair_time)
                if time_diff < delta_time and scan_pair_flag:

                    # Read in the scan
                    alt_scan_df = pd.read_csv(near_fname)

                    # Filter the scan
                    alt_msk_da, alt_peak, msg = filter_scan(
                        alt_scan_df, min_scd, max_scd, min_int, max_int,
                        plume_scd, good_scan_lim, sg_window, sg_polyn
                    )

                    # If the alt scan is good, calculate the plume altitude
                    if alt_msk_da is None:
                        near_fname = 'None'
                        alt_station_name = None
                        plume_alt = default_alt
                        plume_az = default_az
                    else:
                        alt_station = stations[alt_name]
                        plume_alt, plume_az = calc_plume_altitude(
                            station, alt_station, peak, alt_peak, vent_loc,
                            default_alt
                        )
                        alt_station_name = alt_station.name

                # If scans are too far appart, use default values
                else:
                    near_fname = 'None'
                    alt_station_name = None
                    plume_alt = default_alt
                    plume_az = default_az

            # Calculate the scan flux
            filter_idx = msk_scan_da['filter']
            flux_amt, flux_err = calc_scan_flux(
                angles=msk_scan_da['angle'].data[filter_idx],
                scan_so2=[
                    msk_scan_da['SO2'].data[filter_idx],
                    msk_scan_da['SO2_err'].data[filter_idx]
                ],
                station=station,
                vent_location=vent_loc,
                windspeed=wind_speed,
                plume_altitude=plume_alt,
                plume_azimuth=plume_az
            )

            # Add the row to the results dataframe
            row = [scan_time, os.path.split(scan_fname)[1], alt_station_name,
                   os.path.split(near_fname)[1], flux_amt, flux_err, plume_alt,
                   plume_az, wind_speed]
            flux_df.iloc[i] = row

        flux_results[name] = flux_df

    return flux_results


def filter_scan(scan_da, min_scd, max_scd, min_int, max_int, plume_scd,
                good_scan_lim, sg_window, sg_polyn):
    """Filter scans for quality and find the centre."""
    # Filter the points for quality
    mask = np.row_stack([
        scan_da['SO2'] < min_scd,
        scan_da['SO2'] > max_scd,
        scan_da['int_av'] < min_int,
        scan_da['int_av'] > max_int
    ]).any(axis=0)

    if len(np.where(mask)[0]) > good_scan_lim*len(scan_da['SO2']):
        return None, None, 'Not enough good spectra'

    filter_idx = np.all(
        [
            scan_da['SO2'] > min_scd,
            scan_da['SO2'] < max_scd,
            scan_da['int_av'] > min_int,
            scan_da['int_av'] < max_int
        ],
        axis=0
    )
    scan_da['filter'] = xr.DataArray(
        data=filter_idx, coords=scan_da['SO2'].coords
    )

    # Count the number of 'plume' spectra
    nplume = sum(filter_idx < plume_scd)

    if nplume < 10:
        return None, None, 'Not enough plume spectra'

    # Determine the peak scan angle
    x = scan_da['angle'].data[filter_idx]
    y = scan_da['SO2'].data[filter_idx]
    so2_filtered = savgol_filter(y, sg_window, sg_polyn, mode='nearest')
    peak_angle = x[so2_filtered.argmax()]

    return scan_da, peak_angle, 'Scan analysed'


def get_local_scans(stations, fpath):
    """Find all the scans for the given day for all stations.

    Parameters
    ----------
    stations : dict
        Holds the openso2 Station objects.
    fpath : str
        Path to the folder holding the scan data

    Returns
    -------
    scan_fnames : dict
        Dictionary of the scan filenames for each scanner
    scan_times : dict
        Dictionary of the scan timestamps ofr each scanner
    """
    # Initialise empty dictionaries for the file names and timestamps
    scan_fnames = {}
    scan_times = {}

    # For each station find the available scans and there timestamps
    for name in stations:
        try:
            scan_fnames[name] = [
                f'{fpath}/{name}/so2/{f}'
                for f in os.listdir(f'{fpath}/{name}/so2/')
            ]
            scan_times[name] = [
                datetime.strptime(f[:14], '%Y%m%d_%H%M%S')
                for f in os.listdir(f'{fpath}/{name}/so2/')
            ]
        except FileNotFoundError:
            scan_fnames[name] = []
            scan_times[name] = []

    return scan_fnames, scan_times


def find_nearest_scan(station_name, scan_time, scan_fnames, scan_times):
    """Find nearest scan from multiple other stations.

    Parameters
    ----------
    station_name : str
        The station from which the scan is being analysed.
    scan_time : datetime
        The timestamp of the scan
    scan_fnames : dict
        Dictionary of the scan filenames for each scanner
    scan_times : dict
        Dictionary of the scan timestamps ofr each scanner

    Returns
    -------
    nearest_fname : str
        The filepath to the nearest scan
    nearest_timestamp : datetime
        The timestamp of the nearest scan
    """
    # Initialise empty lists to hold the results
    nearest_scan_times = []
    nearest_scan_fnames = []
    nearest_scan_stations = []

    # search through the dictionary of station scans
    for name, fnames in scan_fnames.items():

        # Skip if this is the same station or if there are no scans
        if name == station_name or len(fnames) == 0:
            continue

        # Find the time difference
        delta_times = [abs(t - scan_time).total_seconds()
                       for t in scan_times[name]]

        # Find the closest time
        min_idx = np.argmin(delta_times)

        # Record the nearest scan for that station
        nearest_scan_times.append(scan_times[name][min_idx])
        nearest_scan_fnames.append(fnames[min_idx])
        nearest_scan_stations.append(name)

    # Now find the nearest of the nearest scans
    if len(nearest_scan_times) != 0:
        idx = np.argmin(nearest_scan_times)
        nearest_fname = nearest_scan_fnames[idx]
        nearest_timestamp = nearest_scan_times[idx]
        nearest_station = nearest_scan_stations[idx]

        return nearest_fname, nearest_timestamp, nearest_station

    else:
        return None, None, None


# =============================================================================
# Spinbox
# =============================================================================

# Create a Spinbox object for ease
class DSpinBox(QDoubleSpinBox):
    """Object for generating custom float spinboxes."""

    def __init__(self, value, range):
        """Initialize."""
        super().__init__()
        self.setRange(*range)
        self.setValue(value)


class SpinBox(QSpinBox):
    """Object for generating custom integer spinboxes."""

    def __init__(self, value, range):
        """Initialize."""
        super().__init__()
        self.setRange(*range)
        self.setValue(value)


# =============================================================================
# Widgets Object
# =============================================================================

class Widgets(dict):
    """Object to allow easy config/info transfer with PyQT Widgets."""

    def __init__(self):
        """Initialize."""
        super().__init__()

    def get(self, key):
        """Get the value of a widget."""
        if type(self[key]) == QTextEdit:
            return self[key].toPlainText()
        elif type(self[key]) == QLineEdit:
            return self[key].text()
        elif type(self[key]) == QComboBox:
            return str(self[key].currentText())
        elif type(self[key]) == QCheckBox:
            return self[key].isChecked()
        elif type(self[key]) in [QDateEdit, QDateTimeEdit]:
            return self[key].textFromDateTime(self[key].dateTime())
        elif type(self[key]) in [SpinBox, DSpinBox, QSpinBox, QDoubleSpinBox]:
            return self[key].value()

    def set(self, key, value):
        """Set the value of a widget."""
        if type(self[key]) in [QTextEdit, QLineEdit]:
            self[key].setText(str(value))
        if type(self[key]) == QComboBox:
            index = self[key].findText(value, Qt.MatchFixedString)
            if index >= 0:
                self[key].setCurrentIndex(index)
        if type(self[key]) == QCheckBox:
            self[key].setChecked(value)
        if type(self[key]) in [QDateEdit, QDateTimeEdit]:
            self[key].setDateTime(self[key].dateTimeFromText(value))
        if type(self[key]) in [SpinBox, DSpinBox, QSpinBox, QDoubleSpinBox]:
            self[key].setValue(value)


def browse(gui, widget, mode='single', filter=None):
    """Open native file dialogue."""
    # Check if specified file extensions
    if filter is not None:
        filter = filter + ';;All Files (*)'

    # Pick a single file to read
    if mode == 'single':
        fname, _ = QFileDialog.getOpenFileName(gui, 'Select File', '', filter)

    elif mode == 'multi':
        fname, _ = QFileDialog.getOpenFileNames(gui, 'Select Files', '',
                                                filter)

    elif mode == 'save':
        fname, _ = QFileDialog.getSaveFileName(gui, 'Save As', '', filter)

    elif mode == 'folder':
        fname = QFileDialog.getExistingDirectory(gui, 'Select Folder')

    # Get current working directory
    cwd = os.getcwd() + '/'
    cwd = cwd.replace("\\", "/")

    # Update the relavant widget for a single file
    if type(fname) == str and fname != '':
        if cwd in fname:
            fname = fname[len(cwd):]
        widget.setText(fname)

    # And for multiple files
    elif type(fname) == list and fname != []:
        for i, f in enumerate(fname):
            if cwd in f:
                fname[i] = f[len(cwd):]
        widget.setText('\n'.join(fname))
