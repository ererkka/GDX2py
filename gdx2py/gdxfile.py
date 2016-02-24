# -*- coding: utf-8 -*-
"""
Created on Tue Nov 17 13:29:45 2015

@author: ererkka
"""

import sys
import os.path
from warnings import warn

import pandas as pd
import numpy as np

from gdxcc import *

# String representations of API constants
type_str = {
    GMS_DT_SET: 'set',
    GMS_DT_PAR: 'parameter',
    GMS_DT_VAR: 'variable',
    GMS_DT_EQU: 'equation',
    GMS_DT_ALIAS: 'alias',
}


class GdxFile(object):
    """Class for working with a gdx file

    Use subscripting (gdx_object['<symbolname>']) to get/set a GAMS set or
    parameter symbol as a Pandas Series object. The (MultiLevel) index of
    the series gives the set element names. For a GAMS set, the values are
    associated text labels.

    Parameters
    ----------
    name : str
        Gdx file name
    mode : str
        File open mode: 'r' for reading
    gams_dir : str, optional
        Location of GAMS installation directory

    Attributes
    ----------
    filename : str
        Absolute filename

    Raises
    ------
    RuntimeError
        Unable to load gdx library, invalid mode
    FileNotFoundError
        Input file not found
    ValueError
        Unsupported mode
    OSError
        Unable to read/write file
    Exception
        Other errors
    """

    def __init__(self, filename, mode='r', gams_dir=None):

        self._h = new_gdxHandle_tp()  # Create a gdx handle
        if gams_dir is None:
            ret, err = gdxCreate(self._h, GMS_SSSIZE)
        else:
            ret, err = gdxCreateD(self._h, gams_dir, GMS_SSSIZE)
        if ret == 0:
            raise RuntimeError(err)

        self.filename = os.path.abspath(filename)
        if mode == 'r':
            ret, errno = gdxOpenRead(self._h, self.filename)
        elif mode == 'w':
            ret, errno = gdxOpenWrite(self._h, self.filename,
                                      'Python {}'.format(sys.version))
        elif mode == 'w+' or mode == 'a':
            ret, errno = gdxOpenAppend(self._h, self.filename,
                                      'Python {}'.format(sys.version))
        else:
            raise ValueError("Unsupported mode '{}'.".format(mode))
        self._mode = mode

        # Error checking
        if ret == 0:
            if errno == 2:
                raise FileNotFoundError(self.filename)
            else:
                raise OSError(gdxErrorStr(self._h, errno)[1])

    def __del__(self):
        gdxFree(self._h)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __repr__(self):
        return "GdxFile('{}', mode='{}')".format(self.filename, self._mode)

    def __str__(self):
        header = "GDX file at '{}'\n\n".format(self.filename)
        ret, sym_cnt, uel_cnt = gdxSystemInfo(self._h)
        line = "{:<4}{:20}{:5}{:3}\n"
        symbols = line.format('id','name','type','dim')
        symbols += (4 + 20 + 5 + 3) * '-' + '\n'
        for i in range(1, sym_cnt + 1):
            ret, sym, dim, symtype = gdxSymbolInfo(self._h, i)
            symbols += line.format(i, sym, type_str[symtype][0:3], dim)

        return header + symbols

    def close(self):
        gdxClose(self._h)

    def __getitem__(self, key):
        """Read a GAMS symbol as Pandas Series object
        """
        try:
            symno = int(key)
        except ValueError:
            symno = self._find_symbol(key)
        if symno is None:
            raise KeyError("Symbol '{}' not found.".format(name))

        return self._read_symbol(symno)

    def __setitem__(self, name, data):
        """Store Pandas Series object or a list of tuples into a GAMS symbol
        """
        if self._mode == 'r':
            raise IOError("Cannot write in mode '{}'".format(mode))
        else:
            if self._find_symbol(name) is not None:
                raise NotImplementedError("Cannot replace "
                                          "existing symbol '{}'".format(name))
            else:
                if not isinstance(data, pd.core.series.Series):
                    # Try to convert a list of tuples into
                    try:
                        data = pd.Series(index=pd.MultiIndex.from_tuples(data),
                                         dtype=object)
                    except TypeError:
                        raise RuntimeError("Input data is not a Pandas Series "\
                                           "or a list of tuples")
                if data.dtype == object:
                    self._write_symbol(GMS_DT_SET, name, data, data.name)
                else:
                    self._write_symbol(GMS_DT_PAR, name, data, data.name)

    def _find_symbol(self, name):
        """Find symbol number by name

        Parameters
        ----------
        name : str

        Returns
        -------
        symno : int
        """
        ret, symno = gdxFindSymbol(self._h, name)
        if ret > 0:
            return symno
        else:
            return None

    def _get_expl_text(self, symno):
        ret, recs, user_info, expl_text = gdxSymbolInfoX(self._h, symno)
        if ret > 0:
            return expl_text
        else:
            return None

    def _readstrstart(self, symno):
        """Start string reading at given symbol

        Parameters
        ----------
        symno : int
            Symbol number

        Returns
        -------
        rec : int
            Number of records available

        Raises
        ------
        Exception
        """
        ret, recs = gdxDataReadStrStart(self._h, symno)
        if ret == 0:
            raise Exception(gdxErrorStr(self._h, gdxGetLastError(self._h))[1])
        else:
            return recs

    def _writestrstart(self, symname, symtype, dim, expl_text = ""):
        """Start writing a symbol using strings

        Parameters
        ----------
        symname : str
            Symbol name
        symtype : str
            Type of the symbol
        dim : int
            Dimension of the symbol
        expl_text : str
            Symbol explanatory text

        Returns
        -------
        rec : int
            Number of records available

        Raises
        ------
        Exception
        """

        ret = gdxDataWriteStrStart(self._h, symname, expl_text,
                                   dim, symtype, 0)
        if ret == 0:
            raise Exception(gdxErrorStr(self._h, gdxGetLastError(self._h))[1])
        else:
            return None

    def _read_symbol(self, symno):
        """Read a GAMS symbol as a Pandas Series object

        Parameters
        ----------
        symtype : int
            Symbol number

        """

        # Get symbol info
        ret, sym, dim, symtype = gdxSymbolInfo(self._h, symno)
        if not ret:
            warn("Symbol not found!")
            return None

        # Start reading as strings
        recs = self._readstrstart(symno)

        # Initialize keys and values arrays
        keys = np.empty(recs, dtype=tuple)
        if symtype == GMS_DT_SET:
            dtype = 'U254'  # max. length for set associated text is 254 chars.
        elif symtype == GMS_DT_PAR:
            dtype = 'f8'
        values = np.empty(recs, dtype=dtype)

        for i in range(recs):
            # Read GDX data
            ret, key, value, afdim = gdxDataReadStr(self._h)
            value = value[GMS_VAL_LEVEL]
            keys[i] = tuple(key)
            # For sets, read associated text and store as the value
            if symtype == GMS_DT_SET:
                ret, assoc_text, node = gdxGetElemText(self._h, int(value))
                if ret > 0:
                    values[i] = assoc_text
                else:
                    values[i] = None
            # For parameters, read the value
            elif symtype == GMS_DT_PAR:
                values[i] = value

        gdxDataReadDone(self._h)
        try:
            idx = pd.MultiIndex.from_tuples(keys)
        except ValueError:
            idx = None
        return pd.Series(values, index=idx, name=self._get_expl_text(symno))

    def _write_symbol(self, symtype, symname, data, expl_text = ""):
        """Write a Pandas series to a GAMS Set symbol

        Parameters
        ----------
        symtype : int
            GAMS data type number
        symname : str
            Symbol name
        data : pandas.core.series.Series
            Input data. See class docstring for more info.
        expl_text : str, optional
            Symbol explanatory text

        Raises
        ------
        RuntimeError
            Error registering set associated name to string table.
        Exception
            Other errors
        """

        # Get number of dimensions
        try:
            dims = len(data.index.levels)
        except AttributeError:
            dims = 1

        # Begin writing to a symbol
        try:
            self._writestrstart(symname, symtype, dims, expl_text)
        except:
            raise

        # Init indices and value arrays
        key = GMS_MAX_INDEX_DIM * ['']
        value = doubleArray(GMS_VAL_MAX)

        for i in range(len(data.index)):
            key = data.index[i]
            if isinstance(key, tuple):
                key = [str(k) for k in key]
            else:
                key = [str(key)]
            # For sets, register the associated text in the string table and
            # store index number as the value
            if symtype == GMS_DT_SET:
                val = data.iloc[i]
                if not pd.isnull(val):
                    assoc_text = str(val)
                    ret, idx = gdxAddSetText(self._h, assoc_text)
                    if ret > 0:
                        value[GMS_VAL_LEVEL] = idx
                    else:
                        raise RuntimeError("Unable to register string '{}' "
                                           "to string table".format(assoc_text))
                else:
                    value[GMS_VAL_LEVEL] = 0
            # For parameters, store the value of the parameter as a float
            elif symtype == GMS_DT_PAR:
                value[GMS_VAL_LEVEL] = float(data.iloc[i])

            gdxDataWriteStr(self._h, key, value)

        gdxDataWriteDone(self._h)

        return None