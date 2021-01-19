ThorCam
==========

[![Github Build Status](https://github.com/matham/thorcam/workflows/Python%20application/badge.svg)](https://github.com/matham/thorcam/actions)

Python interface for the Thor scientific Cameras using .Net.

This library does **not** support the **DCx cameras**, only the **scientific cameras**.

For more information and to get started: https://matham.github.io/thorcam/index.html.

To install https://matham.github.io/thorcam/installation.html.

Basic example
-------------

First create a subclass that prints the camera results:

```python
from thorcam.camera import ThorCam
class MyThorCam(ThorCam):
    def received_camera_response(self, msg, value):
        super(MyThorCam, self).received_camera_response(msg, value)
        if msg == 'image':
            return
        print('Received "{}" with value "{}"'.format(msg, value))
    def got_image(self, image, count, queued_count, t):
        print('Received image "{}" with time "{}" and counts "{}", "{}"'
              .format(image, t, count, queued_count))
```

Then use the camera:

```python
>>> # create camera
>>> cam = MyThorCam()
<__main__.MyThorCam at 0x25a72f6a748>
>>> # start the server etc.
>>> cam.start_cam_process()
>>> # get list of attached cams
>>> cam.refresh_cameras()
Received "serials" with value "['05761']"
>>> # open the camera
>>> cam.open_camera('05761')
Received "settings" with value "{'binning_x': 1, 'binning_x_range': [1, 24], ..."
Received "cam_open" with value "None"
>>> cam.exposure_range
[0.0, 1000000.0]
>>> cam.exposure_ms
241.948
>>> # update the exposure value
>>> cam.set_setting('exposure_ms', 150)
Received "settings" with value "{'exposure_ms': 150.0}"
>>> cam.exposure_ms
150.0
>>> # now play the camera
>>> cam.play_camera()
Received "playing" with value "True"
Received image "<ffpyplayer.pic.Image object at 0x000001D1D8D67900>" with time "2e-07" and counts "1", "1"
Received image "<ffpyplayer.pic.Image object at 0x000001D1D8D67990>" with time "0.2310473" and counts "2", "1"
Received image "<ffpyplayer.pic.Image object at 0x000001D1D8D67A68>" with time "0.4735178" and counts "3", "1"
Received image "<ffpyplayer.pic.Image object at 0x000001D1D8D67B40>" with time "0.7157285" and counts "4", "1"
Received image "<ffpyplayer.pic.Image object at 0x000001D1D8D67C18>" with time "0.9583721" and counts "5", "1"
>>> # now stop playing
>>> cam.stop_playing_camera()
Received "playing" with value "False"
>>> # close the camera
>>> cam.close_camera()
Received "cam_closed" with value "None"
>>> # close the server and everything
>>> cam.stop_cam_process(join=True)
```
