.. _install-thorcam:

*************
Installation
*************

Before using the Thor camera, the Thor drivers must be properly installed
(this project has only been tested with USB cameras). See
Thor's website for instruction. Once the cameras are properly recognized
by Windows, ThorCam can be installed using the python wheel from pypi::

    pip install thorcam

The wheels come with the thor .NET binaries required to access the cameras.

If installing from source, e.g.::

    pip install https://github.com/matham/thorcam/archive/master.zip

these binaries must be manually provided. They can be found on Thor's
`website <https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=ThorCam>`_
under "Windows SDK and Doc. for Scientific Cameras". The dlls path must be
provided to the library so it can load them.
See :attr:`thorcam.camera.ThorCamClient.thor_bin_path` and :attr:`thorcam.dep_bins`
for how we handle the dll path.
