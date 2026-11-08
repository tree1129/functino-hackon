"""Persisted SDK service configuration with reference metadata groups."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

SERVICES = ("CONTROL", "NAVIGATION", "MOTION_PLAN", "LEFT_ARM_CAMERA", "RIGHT_ARM_CAMERA", "FRONT_HEAD_CAMERA", "SURROUND_CAMERAS")
SERVICE_TITLES = {"CONTROL":"控制服务", "NAVIGATION":"导航服务", "MOTION_PLAN":"运动规划", "LEFT_ARM_CAMERA":"左臂相机", "RIGHT_ARM_CAMERA":"右臂相机", "FRONT_HEAD_CAMERA":"头部相机", "SURROUND_CAMERAS":"环视相机"}
PREVIEW = {"CONTROL":{"wbc_rate":100.0,"report_error_skip":5,"group_lists":["chassis","head","left_arm","right_arm"]},"NAVIGATION":{"use_static_map":True,"task_timeout_sec":120.0,"max_velocity":0.8},"MOTION_PLAN":{"plan_timeout":5.0,"state_check_resolution":0.01,"enable_collision_check_log":False},"LEFT_ARM_CAMERA":{"color_width":1280,"color_height":1024,"frame_rate":30,"ir_enabled":False},"RIGHT_ARM_CAMERA":{"color_width":1280,"color_height":1024,"frame_rate":30,"ir_enabled":False},"FRONT_HEAD_CAMERA":{"color_width":1280,"color_height":992,"frame_rate":30},"SURROUND_CAMERAS":{"color_width":1280,"color_height":992,"frame_rate":30}}

def _kind(value: Any, meta: dict[str, Any] | None = None) -> str:
    kind = (meta or {}).get("type")
    if kind:
        return {"str":"string", "string":"string", "double":"float", "boolean":"bool",
                "List[float]":"float_array", "List[int]":"float_array",
                "List[List[float]]":"float_array", "List[str]":"string_array"}.get(kind, kind)
    if isinstance(value, bool): return "bool"
    if isinstance(value, int): return "int"
    if isinstance(value, float): return "float"
    if isinstance(value, list): return "string_array" if all(isinstance(v, str) for v in value) else "float_array"
    return "string"

class ConfigurationService:
    def __init__(self, session, live: bool) -> None:
        self.session, self.live = session, live
        self.preview = {name: dict(values) for name, values in PREVIEW.items()}
        path = Path(__file__).resolve().parents[3] / "frontend" / "config_metadata.json"
        try: data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): data = {}
        self.metadata = data.get(getattr(session, "model", "g1"), {})

    def services(self): return [{"id": n, "title": SERVICE_TITLES[n]} for n in SERVICES]

    def _metadata_for(self, service: str, key: str) -> dict[str, Any]:
        """Match literal metadata first, then the reference dynamic-key templates."""
        for meta in self.metadata.get(service, []):
            pattern = re.escape(meta["key"])
            pattern = pattern.replace(re.escape("{链}"), r"(?:head|left_arm|right_arm|torso|leg)")
            pattern = pattern.replace(re.escape("{chain}"), r"(?:head|left_arm|right_arm|torso|leg)")
            pattern = pattern.replace(re.escape("{group}"), r"[^.]+")
            if re.fullmatch(pattern, key): return meta
        return {}
    def _values(self, service: str, defaults=False):
        if service not in SERVICES: raise ValueError("unknown configuration service")
        if not self.live:
            values = dict(self.preview[service])
            for meta in self.metadata.get(service, []):
                key = meta["key"]
                if "{" in key:
                    continue
                if key not in values:
                    kind = _kind(None, meta)
                    values[key] = {"bool":False,"int":0,"float":0.0,"float_array":[0.0,0.0,0.0],"string_array":[],"enum":(meta.get("options") or [""])[0],"string":""}.get(kind, "")
            return values
        robot, sdk = self.session.platform.require_robot()
        status, fields = robot.get_config(getattr(sdk.ConfigService, service), [], defaults)
        return {field.key: field.value for field in fields}

    def schema(self, service: str):
        values, defaults = self._values(service), self._values(service, True)
        grouped = {}
        for key, value in sorted(values.items()):
            meta = self._metadata_for(service, key); gid = meta.get("group", "服务参数")
            group = grouped.setdefault(gid, {"id":gid,"title":gid,"title_en":meta.get("group_title_en", gid),"fields":[]})
            field = {"key":key,"label":meta.get("label", key),"type":_kind(value, meta),"value":value,"readonly":bool(meta.get("readonly",False)),"description":meta.get("description", "当前设备公开配置项；参考文档中未找到该字段的说明"),"description_en":meta.get("description_en", "Public configuration field")}
            for source in ("min","max","step","length","options"):
                if source in meta: field[source] = meta[source]
            if key in defaults: field["default"] = defaults[key]
            group["fields"].append(field)
        return {"service":service,"title":SERVICE_TITLES[service],"groups":list(grouped.values()),"preview":not self.live}

    def save(self, service: str, changes: dict[str, Any]):
        schema = self.schema(service); defs = {f["key"]:f for g in schema["groups"] for f in g["fields"]}; errors={}; converted={}
        for key, value in changes.items():
            rule = defs.get(key)
            if not rule: errors[key] = "未知或当前机型不支持的配置项"; continue
            if rule["readonly"]: errors[key] = "该配置项为只读项，网页不允许修改"; continue
            try:
                kind=rule["type"]
                if kind == "bool" and not isinstance(value,bool): raise ValueError("请输入布尔值")
                if kind == "int" and (isinstance(value,bool) or not isinstance(value,int)): raise ValueError("请输入整数")
                if kind == "float": value=float(value)
                if kind in ("float_array","string_array"):
                    if not isinstance(value,list): raise ValueError("请输入数组")
                    value=[float(x) for x in value] if kind == "float_array" else value
                if rule.get("options") and value not in rule["options"]: raise ValueError("请选择有效选项")
                if rule.get("length") is not None and len(value) != rule["length"]: raise ValueError(f"长度必须为 {rule['length']}")
                nums=value if isinstance(value,list) else [value]
                if rule.get("min") is not None and any(isinstance(x,(int,float)) and x < rule["min"] for x in nums): raise ValueError(f"必须不小于 {rule['min']}")
                if rule.get("max") is not None and any(isinstance(x,(int,float)) and x > rule["max"] for x in nums): raise ValueError(f"必须不大于 {rule['max']}")
                converted[key]=value
            except (TypeError,ValueError) as exc: errors[key]=str(exc) or "格式或取值不合法"
        if errors: raise ValueError(json.dumps({"errors":errors,"message":"请修正标记的字段"}, ensure_ascii=False))
        if not self.live: self.preview[service].update(converted); return {"ok":True,"message":"预览模式：修改仅保存在当前进程"}
        robot,sdk=self.session.platform.require_robot(); status=robot.set_config(getattr(sdk.ConfigService,service), [sdk.ConfigItem(k,v) for k,v in converted.items()])
        if status != sdk.ControlStatus.SUCCESS: raise ValueError(f"SDK rejected configuration: {status}")
        return {"ok":True,"message":"配置已保存；请按设备要求重启相关服务后生效"}
