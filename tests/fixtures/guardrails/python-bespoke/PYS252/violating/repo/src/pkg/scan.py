from pathlib import Path
import os
import subprocess


class ScanAnalyzer:
    def run(self) -> str:
        return os.environ.get('HOME', '')
