from thorcam.camera import ThorCam
import time


def test_internal_process():
    cam = ThorCam()
    cam.start_cam_process()

    time.sleep(5)
    assert cam._server_thread is not None
    assert cam._client_thread is not None
    cam.stop_cam_process(join=True)

    assert cam._server_thread is None
    assert cam._client_thread is None
