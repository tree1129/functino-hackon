from .python_sdk import PythonSdkPlatform


class G3Platform(PythonSdkPlatform):
    def __init__(self) -> None:
        super().__init__("g3")
