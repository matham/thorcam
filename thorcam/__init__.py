"""Thor Cam
=================

Python interface to the .NET Thor cameras.
"""

__version__ = '0.1.2'

import sys
import os
from os.path import join, isdir

__all__ = ('dep_bins', )

dep_bins = []
'''A list of paths to the binaries used by the library. It can be used during
packaging for including required binaries.

If ``THORCAM_NET_BIN_PATH`` is specified in the environment, then we use
that path for the binaries and it's added as the first entry.

Otherwise, the first entry is ``share/thorcam/bin``, if that path exists.
That's the path used by default to locate the Thor .NET binaries. The wheel
provides these binaries at that path, or they can be manually added there.

It is read only.
'''

_bins = os.path.abspath(join(sys.prefix, 'share', 'thorcam', 'bin'))
_env_bins = os.environ.get('THORCAM_NET_BIN_PATH', None)
if _env_bins is not None and isdir(os.path.abspath(_env_bins)):
    dep_bins = [os.path.abspath(_env_bins)]
elif isdir(_bins):
    dep_bins = [_bins]
elif hasattr(sys, '_MEIPASS'):
    dep_bins = [os.path.abspath(sys._MEIPASS)]
