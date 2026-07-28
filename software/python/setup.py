"""
Minimal pip-installable package for the CaduceusCore Python runtime binding.

Install from a release prefix:
    pip install build/install/share/caduceus/python/

The installed `caduceus_runtime` module discovers `libcaduceus_runtime.so` via:
  1. CADUCEUS_RUNTIME_LIB environment variable
  2. The lib/ directory adjacent to the installed Python package
  3. Default fallback paths (build/software/, /usr/local/lib/)
"""

import os
import sys
from setuptools import setup, find_packages

# Package version — keep in sync with the ABI version in runtime.h
__version__ = "1.0.0"

readme = os.path.join(os.path.dirname(__file__), "..", "..", "README.md")
long_description = ""
if os.path.exists(readme):
    with open(readme, encoding="utf-8") as f:
        long_description = f.read()

setup(
    name="caduceus_runtime",
    version=__version__,
    description="CaduceusCore NPU Host Runtime — Python ctypes bindings",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="CaduceusCore Team",
    license="MIT",
    python_requires=">=3.8",
    py_modules=["caduceus_runtime"],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: System :: Hardware :: Hardware Drivers",
    ],
)
