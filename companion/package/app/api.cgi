#!/bin/python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
import webapi  # noqa: E402

webapi.cgi_main()
