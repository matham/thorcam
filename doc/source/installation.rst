.. _install-thorcam:

*************
Installation
*************

Before using the Thor camera, the Thor drivers must be properly installed
(this project has only been tested with USB cameras and doesn't work for
**DCx cameras**). See Thor's website for instruction. Once the cameras are
properly recognized by Windows, ThorCam can be installed with pip.

Native Python
-------------

To install in a native Python installation just do::

    pip install thorcam

The wheels come with the thor .NET binaries required to access the cameras.

If you run into issues, try installing it in a virtual environment because it's likely
some package installed in your environment has some binary conflict. Do as follows::

    python -m pip install --upgrade pip setuptools virtualenv
    python -m virtualenv venv
    # if you're running in a windows cmd terminal do:
    venv\Scripts\activate
    # otherwise if running in a bash-style terminal do:
    source venv\Scripts\activate
    pip install thorcam

then test ``thorcam`` as shown below.

Anaconda
--------

To install in Anaconda, care must be taken to install all the dependencies from
conda before installing ``thorcam`` itself from pip. Otherwise it won't work.
Also, it should be installed in its own environment in case other packages installed
interfere with the dependencies. Do as follows::

    # create the environment
    conda create -n thor python=3
    # activate it
    conda activate thor
    # install dependencies
    conda install -c conda-forge numpy ffpyplayer pythonnet ruamel.yaml ruamel.yaml.clib
    # install throcam
    pip install thorcam

then test ``thorcam`` as shown below.

Test install
------------

To test if it was properly installed, run the following command::

    python -c "from thorcam.camera import ThorCam; import time; cam = ThorCam(); cam.start_cam_process(); cam.refresh_cameras(); time.sleep(5); print(cam.serials); cam.stop_cam_process(join=True)"

this should print a list of serial numbers of cameras connected. If it also prints::

    EDT pdv open failed.
    Check board installation and unit number, and try restarting the computer

just ignore it as it doesn't seem to affect the camera functioning and it's unclear the
source of this.

Installing from source
----------------------

If installing from source rather then using a pre-compiled wheel containing all
the thor binary dependencies, first make sure the thor binaries are manually
provided. They can be found on Thor's
`website <https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=ThorCam>`_
under "Windows SDK and Doc. for Scientific Cameras". The dlls path must be
provided to the library so it can load them.
See :attr:`thorcam.camera.ThorCamClient.thor_bin_path` and :attr:`thorcam.dep_bins`
for how we handle the dll path. Them do::

    pip install https://github.com/matham/thorcam/archive/master.zip
