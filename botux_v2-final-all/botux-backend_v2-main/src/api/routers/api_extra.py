from api.routers.compat import api_extra_router as _compat_api_extra

for _name, _value in vars(_compat_api_extra).items():
    if _name.startswith("__"):
        continue
    globals()[_name] = _value
