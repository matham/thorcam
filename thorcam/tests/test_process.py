from thorcam.camera import ThorCam
import time


class MyThorCam(ThorCam):

    __last_exception = None

    process_connection_timeout = 20

    def handle_exception(self, e, exc_info):
        super().handle_exception(e, exc_info)
        self.__last_exception = e

    def assert_no_exception(self):
        if self.__last_exception is not None:
            raise TypeError(self.__last_exception)

    def wait_until_connected(self):
        ts = time.perf_counter()
        while not self.process_connected and time.perf_counter() - ts < 20:
            time.sleep(1)

        if not self.process_connected:
            raise TimeoutError


def test_internal_process():
    cam = MyThorCam()
    cam.start_cam_process()

    try:
        cam.wait_until_connected()
        assert cam._server_thread is not None
        assert cam._client_thread is not None
    finally:
        cam.stop_cam_process(join=True, kill_delay=5)

    assert cam._server_thread is None
    assert cam._client_thread is None

    cam.assert_no_exception()


def test_list_cams():
    cam = MyThorCam()
    cam.start_cam_process()

    try:
        cam.wait_until_connected()

        old_val = cam.serials
        cam.refresh_cameras()
        time.sleep(2)
        assert cam.serials is not old_val
    finally:
        cam.stop_cam_process(join=True, kill_delay=5)

    cam.assert_no_exception()
