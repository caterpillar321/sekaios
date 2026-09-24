"""창 클래스(app_id) → 앱 아이콘.

창의 클래스 이름이 아이콘 이름과 같지 않은 앱이 많다
(예: 클래스 'org.xfce.mousepad', 'Chromium', 'thunar').
그래서 .desktop 항목을 찾아 그 아이콘을 쓴다.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gio  # noqa: E402

_cache = {}


def _lookup(cls):
    if cls in _cache:
        return _cache[cls]
    theme = Gtk.IconTheme.get_default()
    gicon = None
    low = cls.lower()
    # 1. .desktop 이름이 클래스와 같은 경우 (가장 흔하다)
    for cand in (cls, low, low.split(".")[-1]):
        try:
            app = Gio.DesktopAppInfo.new(cand + ".desktop")
        except TypeError:
            app = None
        if app and app.get_icon():
            gicon = app.get_icon()
            break
    # 2. StartupWMClass 가 맞는 항목
    if gicon is None:
        for app in Gio.AppInfo.get_all():
            try:
                wm = app.get_startup_wm_class()
            except AttributeError:
                wm = None
            if wm and wm.lower() == low and app.get_icon():
                gicon = app.get_icon()
                break
    # 3. 테마에 클래스 이름 그대로의 아이콘
    if gicon is None:
        for cand in (cls, low, low.split(".")[-1]):
            if cand and theme.has_icon(cand):
                gicon = Gio.ThemedIcon.new(cand)
                break
    if gicon is None:
        gicon = Gio.ThemedIcon.new("application-x-executable")
    _cache[cls] = gicon
    return gicon


def app_gicon(cls):
    return _lookup(cls or "")


def app_icon(cls, size):
    img = Gtk.Image.new_from_gicon(_lookup(cls or ""), Gtk.IconSize.DIALOG)
    img.set_pixel_size(size)
    return img
