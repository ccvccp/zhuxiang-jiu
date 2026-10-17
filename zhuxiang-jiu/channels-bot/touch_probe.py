# 触摸注入 API 最小验证 (ctypes, 结构自检+错误码)
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)


class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ('pointerType', wintypes.DWORD),
        ('pointerId', wintypes.DWORD),
        ('frameId', wintypes.DWORD),
        ('pointerFlags', wintypes.DWORD),
        ('hWndTarget', wintypes.HWND),
        ('ptPixelLocation', wintypes.POINT),
        ('ptHimetricLocation', wintypes.POINT),
        ('ptPixelLocationRaw', wintypes.POINT),
        ('ptHimetricLocationRaw', wintypes.POINT),
        ('dwTime', wintypes.DWORD),
        ('historyCount', wintypes.DWORD),
        ('InputChannel', wintypes.INT),
        ('dwKeyStates', wintypes.DWORD),
        ('PerformanceCount', ctypes.c_uint64),
    ]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [
        ('pointerInfo', POINTER_INFO),
        ('touchFlags', wintypes.DWORD),
        ('touchMask', wintypes.DWORD),
        ('rcContact', wintypes.RECT),
        ('orientation', wintypes.DWORD),
        ('pressure', wintypes.DWORD),
    ]


print('POINTER_INFO size =', ctypes.sizeof(POINTER_INFO), '(expect 80)')
print('POINTER_TOUCH_INFO size =', ctypes.sizeof(POINTER_TOUCH_INFO), '(expect 112)')

user32.InitializeTouchInjection.argtypes = [wintypes.UINT, wintypes.DWORD]
user32.InjectTouchInput.argtypes = [wintypes.UINT, ctypes.POINTER(POINTER_TOUCH_INFO)]
user32.InjectTouchInput.restype = wintypes.BOOL

ok = user32.InitializeTouchInjection(2, 1)
print('InitializeTouchInjection =', ok, 'err =', ctypes.get_last_error())

# 触摸点击 (960, 300) 空白区域
FL_INRANGE, FL_INCONTACT, FL_PRIMARY, FL_CONF = 0x2, 0x4, 0x100, 0x400
FL_DOWN, FL_UP = 0x10000, 0x40000
TM_ALL = 0x7

x, y = 960, 300
info = POINTER_TOUCH_INFO()
info.pointerInfo.pointerType = 2
info.pointerInfo.pointerId = 0
info.pointerInfo.frameId = 0
info.pointerInfo.ptPixelLocation.x, info.pointerInfo.ptPixelLocation.y = x, y
info.pointerInfo.ptHimetricLocation.x, info.pointerInfo.ptHimetricLocation.y = int(x * 26.46), int(y * 26.46)
info.touchFlags = 0
info.touchMask = TM_ALL
info.rcContact.left, info.rcContact.right = x - 6, x + 6
info.rcContact.top, info.rcContact.bottom = y - 6, y + 6
info.orientation = 90
info.pressure = 512

info.pointerInfo.pointerFlags = FL_DOWN | FL_INRANGE | FL_INCONTACT | FL_PRIMARY | FL_CONF
d1 = user32.InjectTouchInput(1, ctypes.byref(info))
e1 = ctypes.get_last_error()
print('down =', d1, 'err =', e1)

import time
time.sleep(0.12)
info.pointerInfo.pointerFlags = FL_UP | FL_INRANGE | FL_PRIMARY | FL_CONF
info.pressure = 0
d2 = user32.InjectTouchInput(1, ctypes.byref(info))
e2 = ctypes.get_last_error()
print('up =', d2, 'err =', e2)
print('DONE')
