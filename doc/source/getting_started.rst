Getting Started
================

Introduction
-------------

ThorCam provides a Python interface to the Thor scientific cameras .NET interface.

Thor provides a .NET interface to their scientific camera. This project
uses ``pythonnet`` for access to the .NET interface. Due to potential dll
incompatibility issues between these thor/.NET dlls and other python libraries,
we create a second internal process in which we load and control the camera
to isolate it from the main python process.

:mod:`thorcam.camera` provides a object oriented interface to the camera in the
second process so the camera can be configured and played etc.
See :ref:`thorcam-examples`.

Usage
------

To use thorcam, you first need to install it, see :ref:`install-thorcam`.

After it's installed, it can be used like any python library.
See :ref:`thorcam-examples` for examples on how to use it. Complete API
documentation is at :ref:`ThorCam API <thorcam-root-api>`.
