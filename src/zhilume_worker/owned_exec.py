"""Linux exec wrapper: the owned inference process dies with its Worker parent."""
import ctypes
import os
import signal
import sys

if sys.platform != 'linux' or ctypes.CDLL(None).prctl(1, signal.SIGKILL) != 0 or os.getppid() != int(sys.argv[1]):
    raise SystemExit('Parent lifetime protection unavailable')
os.execv(sys.argv[2], sys.argv[2:])
