import copy
import numpy as np
from collections import OrderedDict


# =============================================================================
# Parameters
# =============================================================================

class Parameters(OrderedDict):
    """Collection of Parameter objects.

    An ordered dictionary of all the Paramter objects that will be included
    in the forward model. Each Parameter has a single entry with a string
    label, value and boolian control on whether it is varied in the fit.

    Parameters
    ----------
    None

    Attributes
    ----------
    None
    """

    def __init__(self, *args, **kwargs):
        """Initialize the Parameters."""
        self.update(*args, **kwargs)

    def add(self, name, value=0, vary=True, xpath=None, plume_gas=False,
            lo_bound=-np.inf, hi_bound=np.inf):
        """Add a Parameter to the Parameters object.

        Parameters
        ----------
        name : str
            Identifier string for the parameter. Each must be unique
        value : float, optional
            The initial numerical parameter value. Default is 0
        vary : bool, optional
            If True then the parameter is fitted. Otherwise it is fixed to its
            value. Default is True
        xpath : str, optional
            The file path to the cross-section file, used for gas parameters.
            Default is None
        plume_gas : bool, optional
            Marks an absorber as a plume species. Used when analysing for light
            dilution. Default is False.
        lo_bound : float, optional
            The lower bound of the allowed variation of the Parameter. Default
            is -inf
        hi_bound : float, optional
            The higher bound of the allowed variation of the Parameter. Default
            is +inf
        """
        self.__setitem__(name, Parameter(name=name,
                                         value=value,
                                         vary=vary,
                                         xpath=xpath,
                                         plume_gas=plume_gas,
                                         lo_bound=lo_bound,
                                         hi_bound=hi_bound))

    def add_many(self, param_list):
        """Add multiple Parameters to the Parameters object.

        Parameters
        ----------
        param_list : list of Parameter like objects
        """
        for param in param_list:

            self.__setitem__(param.name, param)

    def update_values(self, new_values):
        """Update the values of each Parameter in order."""
        n = 0
        for name in self:
            if self[name].vary:
                self[name].set(value=new_values[n])
                n += 1

    def valuesdict(self):
        """Return an ordered dictionary of all parameter values."""
        return OrderedDict((p.name, p.value) for p in self.values())

    def fittedvaluesdict(self):
        """Return an ordered dictionary of fitted parameter values."""
        return OrderedDict((p.name, p.value) for p in self.values() if p.vary)

    def popt_dict(self):
        """Return a dictionary of the optimised parameters."""
        return OrderedDict((p.name, p.fit_val)
                           for p in self.values() if p.vary)

    def valueslist(self):
        """Return a list of all parameter values."""
        return [(p.value) for p in self.values()]

    def fittedvalueslist(self):
        """Return a list of the fitted parameter values."""
        return [(p.value) for p in self.values() if p.vary]

    def popt_list(self):
        """Return a list of the optimised parameters."""
        return [(p.fit_val) for p in self.values() if p.vary]

    def bounds(self):
        """Return a list of the low and high bounds."""
        return [[(p.lo_bound) for p in self.values() if p.vary],
                [(p.hi_bound) for p in self.values() if p.vary]]

    def make_copy(self):
        """Return a deep copy of the Parameters object."""
        return copy.deepcopy(self)

    def pretty_print(self, mincolwidth=7, precision=4, cols='basic'):
        """Print the parameters in a nice way.

        Parameters
        ----------
        mincolwidth : int, optional
            Minimum width of the columns. Default is 7
        precision : int, optional
            Number of significant figures to print to. Default is 4
        cols : str or list, optional
            The columns to be printed. Either "all" for all columns, "basic"
            for the name, value and if it is fixed or a list of the desired
            column names. Default is "basic"

        Returns
        -------
        msg : str
            The formatted message to print
        """
        # Set default column choices
        def_cols = {'all':   ['name', 'value', 'vary', 'fit_val', 'fit_err',
                              'xpath'],
                    'basic': ['name', 'value', 'vary', 'xpath']}

        # Make list of columns
        if cols == 'all' or cols == 'basic':
            cols = def_cols[cols]

        colwidth = [mincolwidth] * (len(cols))

        if 'name' in cols:
            i = cols.index('name')
            colwidth[i] = max([len(name) for name in self]) + 2

        if 'value' in cols:
            i = cols.index('value')
            colwidth[i] = max([len(f'{p.value:.{precision}g}')
                               for p in self.values()]) + 2

        if 'vary' in cols:
            i = cols.index('vary')
            colwidth[i] = mincolwidth

        if 'xpath' in cols:
            i = cols.index('xpath')
            colwidth[i] = max([len(p.xpath) for p in self.values()
                               if p.xpath is not None]) + 2

        if 'fit_val' in cols:
            i = cols.index('fit_val')
            colwidth[i] = max([len(f'{p.fit_val:.{precision}g}')
                               for p in self.values()]) + 2

        if 'fit_err' in cols:
            i = cols.index('fit_err')
            colwidth[i] = max([len(f'{p.fit_err:.{precision}g}')
                               for p in self.values()]) + 2

        for n, w in enumerate(colwidth):
            if w < mincolwidth:
                colwidth[n] = mincolwidth

        # Generate the string
        title = ''
        for n, c in enumerate(cols):
            title += f'|{c:^{colwidth[n]}}'
        title += '|'

        msg = f'\n{"MODEL PARAMETERS":^{len(title)}}\n{title}\n' + \
              f'{"-"*len(title)}\n'

        for name, p in self.items():
            d = {'name': f'{p.name}',
                 'value': f'{p.value:.{precision}g}',
                 'xpath': f'{p.xpath}',
                 'fit_val': f'{p.fit_val:.{precision}g}',
                 'fit_err': f'{p.fit_err:.{precision}g}',
                 'vary': f'{p.vary}'
                 }

            for col in cols:
                msg += f'|{d[col]:^{colwidth[cols.index(col)]}}'

            msg += '|\n'

        return(msg)


# =============================================================================
# Parameter
# =============================================================================

class Parameter(object):
    """A parameter is a value that can be varied in the fit.

    Each parameter has an assosiated name and value and can be set to either
    vary or be fixed in the model

    Based on the Parameter class of lmfit.

    Parameters
    ----------
    name : str
        Identifier string for the parameter. Each must be unique
    value : float
        The initial numerical parameter value
    vary : bool, optional
        If True then the parameter is fitted. Otherwise it is fixed to its
        value. Default is True
    xpath : str, optional
        The file path to the cross-section for this parameter. Default is None
    plume_gas : bool, optional
        Marks an absorber as a plume species. Used when analysing for light
        dilution. Default is False.
    lo_bound : float, optional
        The lower bound of the allowed variation of the Parameter. Default
        is -inf
    hi_bound : float, optional
        The higher bound of the allowed variation of the Parameter. Default
        is +inf

    Attributes
    ----------
    fit_val : float
        The fitted parameter value
    fit_err : float
        The fitted parameter error
    """

    def __init__(self, name, value, vary=True, xpath=None, plume_gas=False,
                 lo_bound=-np.inf, hi_bound=np.inf):
        """Initialise the parameter."""
        self.name = name
        self.value = value
        self.vary = vary
        self.xpath = xpath
        self.plume_gas = plume_gas
        self.lo_bound = lo_bound
        self.hi_bound = hi_bound
        self.fit_val = np.nan
        self.fit_err = np.nan

    def set(self, value=None, vary=None, xpath=None, plume_gas=None,
            lo_bound=None, hi_bound=None, fit_val=None, fit_err=None):
        """Update the attributes of a Parameter."""
        if value is not None:
            self.value = value

        if vary is not None:
            self.vary = vary

        if xpath is not None:
            self.xpath = xpath

        if plume_gas is not None:
            self.plume_gas = plume_gas

        if lo_bound is not None:
            self.lo_bound = lo_bound

        if hi_bound is not None:
            self.hi_bound = hi_bound

        if fit_val is not None:
            self.fit_val = fit_val

        if fit_err is not None:
            self.fit_err = fit_err
