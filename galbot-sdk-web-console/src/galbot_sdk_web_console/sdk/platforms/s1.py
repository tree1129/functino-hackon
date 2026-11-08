from .python_sdk import PythonSdkPlatform


class S1Platform(PythonSdkPlatform):
    def __init__(self) -> None:
        super().__init__("s1")
