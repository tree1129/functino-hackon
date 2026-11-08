from .python_sdk import PythonSdkPlatform


class G1Platform(PythonSdkPlatform):
    def __init__(self) -> None:
        super().__init__("g1")
