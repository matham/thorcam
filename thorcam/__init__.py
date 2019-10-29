"""Thor Cam
=================

Python interface to the .NET Thor cameras.
"""

__version__ = '0.1.0.dev0'

import sys
import os
from os.path import join, isdir

__all__ = ('dep_bins', )

_bins = join(sys.prefix, 'share', 'thorcam', 'bin')
dep_bins = []
'''A list of paths to the binaries used by the library. It can be used during
packaging for including required binaries.

It is read only.
'''

if isdir(_bins):
    os.environ["PATH"] += os.pathsep + _bins
    dep_bins = [_bins]
