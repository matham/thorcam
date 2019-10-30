"""Thor Cam
=================

Python interface to the .NET Thor cameras.
"""

__version__ = '0.1.0.dev0'

import sys
from os.path import join, isdir

__all__ = ('dep_bins', )

dep_bins = []
'''A list of paths to the binaries used by the library. It can be used during
packaging for including required binaries.

The first entry is ``share/thorcam/bin``, if that path exists. That's
the path used by default to locate the Thor .NET binaries. The wheel
provides these binaries at that path, or they can be manually added there.

It is read only.
'''

_bins = join(sys.prefix, 'share', 'thorcam', 'bin')
if isdir(_bins):
    dep_bins = [_bins]
