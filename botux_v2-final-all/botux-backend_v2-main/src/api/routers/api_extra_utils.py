from api.routers.compat import api_extra_utils as _compat_api_extra_utils

for _name, _value in vars(_compat_api_extra_utils).items():
    if _name.startswith("__"):
        continue
    globals()[_name] = _value
